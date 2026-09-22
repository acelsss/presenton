"""Deterministic coordinator above native tools; no model/provider calls.

References serialize input acceptance. Native page changes, cursor and receipts
share a transaction; async_tasks is the sole durable execution state.
"""
import copy
import json
import secrets
import uuid

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy import or_, select
from sqlalchemy.orm.attributes import flag_modified

from enums.async_task_status import AsyncTaskStatus
from models.sql.agent_document import AgentOperationReceipt
from models.sql.async_task import AsyncTaskModel
from models.sql.ppt_workflow import PptWorkflowRef
from models.sql.template_v2 import TemplateV2
from services.agent_tools.assets import ExistingAssets
from services.agent_tools.catalog import OUTLINE_TOOLS, WRITE_RESULTS
from services.agent_tools.errors import OperationRejected
from services.agent_tools.schemas import BatchMutation, ToolRequest
from services.agent_tools.service import AgentDocumentService, digest, fail
from services.agent_tools.workflow_templates import describe, layouts, schema_for
from services.agent_tools.workflow_timing import timed_stage
from services.chat.memory_layer import PresentationChatMemoryLayer

TASK_TYPE = "ppt_workflow"
DEADLINE_SECONDS = 24 * 3600


class WorkflowService(AgentDocumentService):
    def __init__(self, session, owner_id, actor):
        super().__init__(session, owner_id)
        self.actor = digest({"owner": str(owner_id), "client": actor})

    async def begin(self):
        # SQLite has no FOR UPDATE. Acquire its writer reservation before reads,
        # avoiding read-to-write upgrades and serializing concurrent ref acceptance.
        if self.session.bind.dialect.name == "sqlite":
            connection = await self.session.connection()
            active = await connection.run_sync(lambda conn: conn.connection.driver_connection.in_transaction)
            if not active:
                await connection.exec_driver_sql("BEGIN IMMEDIATE")

    async def ref(self, key, kinds, *, actor=True):
        await self.begin()
        ref = await self.session.scalar(select(PptWorkflowRef).where(
            PptWorkflowRef.id == key, PptWorkflowRef.owner_id == self.owner_id,
        ).with_for_update().execution_options(populate_existing=True))
        if ref is None or ref.kind not in kinds:
            fail(404, "workflow_reference_not_found")
        # Ownership is the authorization boundary. Transport sessions and elapsed
        # thinking time never revoke access to a draft or a revision snapshot.
        return ref

    async def new_ref(self, kind, data, document_id=None, task_id=None):
        ref = PptWorkflowRef(owner_id=self.owner_id, actor=self.actor, kind=kind,
                             document_id=document_id, task_id=task_id,
                             expires_at=await self.now() + DEADLINE_SECONDS, data=data)
        self.session.add(ref)
        return ref

    async def load_task(self, task_id):
        await self.begin()
        task = await self.session.scalar(select(AsyncTaskModel).where(
            AsyncTaskModel.id == task_id, AsyncTaskModel.owner_id == self.owner_id,
            AsyncTaskModel.type == TASK_TYPE,
        ).with_for_update().execution_options(populate_existing=True))
        if task is None:
            fail(404, "workflow_task_not_found")
        # SQL JSON columns track assignments, not nested mutation.
        task.payload = copy.deepcopy(task.payload)
        return task

    @staticmethod
    def request_data(request):
        return request if isinstance(request, dict) else request.model_dump(mode="json")

    async def action_ref(self, kind, scope, request, document_id, task_id=None):
        # The document/task row is already locked. Deduplication belongs to the
        # server, scoped to this business operation, never to an LLM-chosen ID.
        key = digest({"owner": str(self.owner_id), "kind": kind, "scope": scope,
                      "request": self.request_data(request)})
        ref = await self.session.get(PptWorkflowRef, key)
        if ref is None:
            ref = PptWorkflowRef(id=key, owner_id=self.owner_id, actor=self.actor,
                kind=kind, document_id=document_id, task_id=task_id, expires_at=0, data={})
            self.session.add(ref)
        return ref

    async def replay(self, ref, request):
        if ref.request_hash is not None:
            if ref.request_hash != digest(self.request_data(request)):
                raise HTTPException(409, {"code": "preparation_already_started",
                    "documentId": str(ref.document_id), "nextTool": "ppt_read",
                    "message": "Edit the existing document with ppt_read/ppt_edit. Prepare again only for an intentional new document."})
            result = {**ref.result, "replayed": True,
                      "receiptStatus": ref.result.get("status"),
                      "receiptRevision": ref.result.get("revision")}
            if ref.task_id:
                # Historical acceptance is not the task's current state. Read
                # compact progress, never the potentially large frozen snapshot.
                keys = ("taskRef", "documentId", "stage", "revision", "slots", "errors",
                        "repairRef", "exports", "previews", "nextTry", "nextEditRef")
                row = (await self.session.execute(select(*[
                    AsyncTaskModel.payload[key].label(key) for key in keys
                ]).where(AsyncTaskModel.id == ref.task_id,
                         AsyncTaskModel.owner_id == self.owner_id))).mappings().one_or_none()
                if row is not None:
                    current = self.status_payload({k: v for k, v in row.items() if v is not None})
                    if current.get("editRef") is None and result.get("editRef"):
                        current.pop("editRef")
                    result.update(current)
            if ref.document_id:
                state, _ = await self.document(ref.document_id)
                result["currentRevision"], result["currentPhase"] = state.revision, state.phase
                if result.get("pages"):
                    versions = await self.page_revisions(state.id)
                    result["currentPages"] = [{"slideId": page["slideId"],
                        "pageRevision": versions.get(uuid.UUID(page["slideId"]), 0)} for page in result["pages"]]
                result["nextTool"] = "ppt_read" if state.phase == "ready" else "ppt_task"
            return result
        return None

    async def accept(self, ref, request, result):
        ref.request_hash = digest(self.request_data(request))
        ref.result = result
        await self.session.commit()
        return result

    @staticmethod
    def status(task):
        return WorkflowService.status_payload(task.payload)

    @staticmethod
    def status_payload(p):
        slots = p.get("slots", [])
        return {"taskRef": p["taskRef"], "documentId": p["documentId"],
                "status": p["stage"], "revision": p["revision"],
                "committed": [s["slotId"] for s in slots if s.get("committed")],
                "staged": [s["slotId"] for s in slots if s.get("candidate") and not s.get("committed")],
                "pending": [s["slotId"] for s in slots if not s.get("committed")],
                "slots": [{"slotId": s["slotId"], "index": s["index"], "layoutId": s["layoutId"],
                           "pageRevision": s.get("pageRevision", 0), "slideId": s.get("slideId")} for s in slots],
                "errors": p.get("errors", []), "repairRef": p.get("repairRef"),
                "exports": p.get("exports", {}), "previews": p.get("previews", []),
                "retryAt": p.get("nextTry"),
                  "visualReviewRequired": bool(p.get("exports")), "editRef": p.get("nextEditRef")}

    async def task_status(self, task_ref):
        # Poll only progress fields. Frozen assets can make payload tens of MB;
        # status must neither hydrate those bytes nor reserve SQLite's writer.
        keys = ("taskRef", "documentId", "stage", "revision", "slots", "errors",
                "repairRef", "exports", "previews", "nextTry", "nextEditRef")
        row = (await self.session.execute(select(*[
            AsyncTaskModel.payload[key].label(key) for key in keys
        ]).join(PptWorkflowRef, PptWorkflowRef.task_id == AsyncTaskModel.id).where(
            PptWorkflowRef.id == task_ref, PptWorkflowRef.kind == "task",
            PptWorkflowRef.owner_id == self.owner_id,
            AsyncTaskModel.owner_id == self.owner_id, AsyncTaskModel.type == TASK_TYPE,
        ))).mappings().one_or_none()
        # End the read transaction before the HTTP long-poll sleeps. This also
        # makes subsequent polls see commits on repeatable-read databases.
        await self.session.rollback()
        if row is None:
            fail(404, "workflow_reference_not_found")
        return self.status_payload({key: value for key, value in row.items() if value is not None})

    @timed_stage
    async def prepare(self, request):
        await self.begin()
        assets = await ExistingAssets.for_document(self.session, self.owner_id, {})
        await assets.validate([{"url": value} for value in request.asset_refs])
        templates = list(await self.session.scalars(select(TemplateV2).where(or_(
            TemplateV2.owner_id == self.owner_id,
            TemplateV2.owner_id.is_(None) & TemplateV2.is_default.is_(True),
        )).order_by(TemplateV2.id)))
        templates = [t for t in templates if (t.layouts or {}).get("layouts")]
        # Deterministic preference only; content/visual choice stays with the agent.
        style = request.style.casefold()
        templates.sort(key=lambda t: (not (style and style in (t.name + " " + t.id).casefold()), t.id))
        chosen = templates[request.template_offset:request.template_offset + 4]
        snapshots, candidates = {}, []
        for template in chosen:
            snapshot = {**copy.deepcopy(template.layouts), "name": f"custom-{template.id}"}
            from services.agent_tools.workflow_export import asset_fingerprints
            manifest = asset_fingerprints(self.owner_id, [snapshot, template.theme])
            snapshot["_agentAssetHashes"] = manifest
            key = digest({"layout": snapshot, "theme": template.theme})
            snapshots[key] = {"id": template.id, "layout": snapshot, "theme": template.theme}
            candidates.append({"templateRef": key, "name": template.name,
                               "layouts": [describe(item) for item in layouts(snapshot).values()]})
        if not candidates:
            fail(422, "no_accessible_templates_at_offset")
        ref = await self.new_ref("prepare", {"input": request.model_dump(mode="json"), "templates": snapshots})
        await self.session.commit()
        return {"preparationRef": ref.id, "templates": candidates,
                "nextTemplateOffset": request.template_offset + 4 if len(templates) > request.template_offset + 4 else None,
                "documentCreated": False}

    @timed_stage
    async def start(self, request):
        ref = await self.ref(request.preparation_ref, {"prepare"})
        replay = await self.replay(ref, request)
        if replay:
            return replay
        template = ref.data["templates"].get(request.template_ref)
        outline = ref.data["input"]["outline"]
        if template is None:
            fail(422, "template_not_in_preparation")
        # Recheck permission, but use the pinned content rather than latest content.
        accessible = await self.session.scalar(select(TemplateV2.id).where(
            TemplateV2.id == template["id"], or_(TemplateV2.owner_id == self.owner_id,
                TemplateV2.owner_id.is_(None) & TemplateV2.is_default.is_(True))))
        if accessible is None:
            fail(403, "template_permission_revoked")
        native_layouts = layouts(template["layout"])
        if len(request.layout_plan) != len(outline) or any(key not in native_layouts for key in request.layout_plan):
            fail(422, "one_available_layout_required_per_outline")
        from services.agent_tools.workflow_export import check_asset_fingerprints
        check_asset_fingerprints(self.owner_id, template["layout"].get("_agentAssetHashes", {}))
        state, presentation = await self.stage_document(ref.data["input"]["title"], ref.data["input"]["language"])
        for content in outline:
            await self._apply_tool(state, presentation, ToolRequest(tool="addOutline", arguments={"content": content}))
        await self._apply_tool(state, presentation, ToolRequest(tool="confirmOutline", arguments={"confirmed": True}))
        presentation.layout = copy.deepcopy(template["layout"])
        presentation.theme = copy.deepcopy(template["theme"])
        state.template_id, state.phase, state.revision = template["id"], "composing", 1
        task = AsyncTaskModel(owner_id=self.owner_id, type=TASK_TYPE, status=AsyncTaskStatus.PENDING)
        task_ref = await self.new_ref("task", {}, presentation.id, task.id)
        edit_ref = await self.new_ref("submit", {}, presentation.id, task.id)
        slots = [{"slotId": secrets.token_urlsafe(16), "index": i, "layoutId": key,
                  "operationId": str(uuid.uuid4()), "committed": False, "pageRevision": 0} for i, key in enumerate(request.layout_plan)]
        task.payload = {"documentId": str(presentation.id), "taskRef": task_ref.id,
                        "actor": self.actor,
                        "revision": 1, "stage": "awaiting_content", "slots": slots,
                        "finish": False, "exportFormats": [],
                        "finalizeOperationId": str(uuid.uuid4()), "repairRef": edit_ref.id}
        state.workflow_task_id = task.id
        self.session.add(task)
        ref.document_id, ref.task_id = presentation.id, task.id
        return await self.accept(ref, request, {
            "documentId": str(presentation.id), "editRef": edit_ref.id, "taskRef": task_ref.id,
            "status": "awaiting_content", "slots": [{k: s[k] for k in ("slotId", "index", "layoutId", "pageRevision")} for s in slots],
            "layouts": [describe(native_layouts[key], full=True) for key in dict.fromkeys(request.layout_plan)],
        })

    async def check_task_revision(self, task, state):
        p = task.payload
        if state.revision != p["revision"]:
            fail(409, "needs_rebase")
        if state.workflow_task_id != task.id:
            fail(409, "task_no_longer_current")

    @timed_stage
    async def submit(self, request):
        source = await self.ref(request.task_ref or request.edit_ref, {"task", "submit", "repair"})
        if request.edit_ref and source.request_hash:
            legacy_input = request.model_dump(mode="json", exclude={"task_ref"})
            for page in legacy_input["pages"]:
                page.pop("expected_page_revision", None)
            if source.request_hash == digest(legacy_input):
                return await self.replay(source, legacy_input)
        task = await self.load_task(source.task_id)
        p = task.payload
        payload = request.model_dump(mode="json", exclude={"task_ref", "edit_ref"})
        ref = await self.action_ref("submit_action", task.id, payload, source.document_id, task.id)
        replay = await self.replay(ref, payload)
        if replay:
            return replay
        if p["stage"] in {"completed", "export_queued", "exporting", "export_input_error"}:
            raise HTTPException(409, {"code": "document_completed_use_ppt_edit",
                "documentId": p["documentId"], "nextTool": "ppt_read",
                "message": "Generation is finished. Use ppt_read then ppt_edit to change this document; do not recreate it."})
        if p["stage"] not in {"awaiting_content", "needs_input", "awaiting_finish"}:
            fail(409, "task_already_running_use_task_status")
        state, presentation = await self.document(ref.document_id)
        await self.check_task_revision(task, state)
        slots = {s["slotId"]: s for s in p["slots"]}
        if len({page.slot_id for page in request.pages}) != len(request.pages):
            fail(422, "duplicate_slot")
        allowed = [key for key, slot in slots.items() if not slot.get("committed")]
        if any(page.slot_id not in allowed or page.slot_id not in slots for page in request.pages):
            fail(422, "only_requested_slots_may_be_repaired")
        for page in request.pages:
            if page.expected_page_revision != slots[page.slot_id].get("pageRevision", 0):
                raise HTTPException(409, {"code": "page_revision_conflict", "slotId": page.slot_id,
                    "currentPageRevision": slots[page.slot_id].get("pageRevision", 0), "nextTool": "ppt_task"})
            slots[page.slot_id]["candidate"] = {**page.content, "__speaker_note__": page.speaker_note}
        assets = await ExistingAssets.for_document(self.session, self.owner_id, presentation.layout)
        from services.agent_tools.workflow_export import asset_fingerprints, check_asset_fingerprints
        check_asset_fingerprints(self.owner_id, presentation.layout.get("_agentAssetHashes", {}))
        native_layouts = layouts(presentation.layout)
        errors = []
        for slot in p["slots"]:
            if slot.get("committed"):
                continue
            content = slot.get("candidate")
            if content is None:
                if request.finish or p["finish"]:
                    errors.append({"slotId": slot["slotId"], "code": "page_missing"})
                continue
            validator = Draft202012Validator(schema_for(native_layouts[slot["layoutId"]]))
            for error in list(validator.iter_errors(PresentationChatMemoryLayer._strip_runtime_fields(content)))[:20]:
                errors.append({"slotId": slot["slotId"], "code": "schema_invalid",
                               "path": list(error.absolute_path), "rule": error.validator,
                               "constraint": error.validator_value})
            try:
                await assets.validate(content)
                slot["assetHashes"] = asset_fingerprints(self.owner_id, content)
            except OperationRejected as exc:
                errors.append({"slotId": slot["slotId"], "code": exc.code})
        invalid = {error["slotId"] for error in errors}
        for page in request.pages:
            if page.slot_id not in invalid:
                slots[page.slot_id]["pageRevision"] = slots[page.slot_id].get("pageRevision", 0) + 1
        p["finish"] = p["finish"] or request.finish
        p["exportFormats"] = list(dict.fromkeys([*p["exportFormats"], *request.export_formats]))
        p["errors"] = errors
        if errors:
            repair = await self.new_ref("repair", {"allowedSlots": list(dict.fromkeys(e["slotId"] for e in errors))}, state.id, task.id)
            p["repairRef"], p["stage"] = repair.id, "needs_input"
        else:
            p["repairRef"] = None
            p["stage"] = "queued" if all(s.get("candidate") or s.get("committed") for s in p["slots"]) else "awaiting_content"
        task.payload = copy.deepcopy(p)
        flag_modified(task, "payload")
        return await self.accept(ref, payload, self.status(task))

    async def add_receipt(self, operation_id, document_id, payload, result):
        self.session.add(AgentOperationReceipt(owner_id=self.owner_id,
            operation_id=uuid.UUID(operation_id), document_id=document_id,
            payload_hash=digest(payload), result=result, created_at=await self.now()))

    @timed_stage
    async def step(self, task_id):
        """One durable page transaction; safe to resume after a process crash."""
        task = await self.load_task(task_id)
        p = task.payload
        self.session.info["ppt_step_cursor"] = (p["revision"], p["stage"])
        self.actor = p["actor"]
        if p["stage"] not in {"queued", "saving"} or p.get("nextTry", 0) > await self.now():
            return self.status(task)
        state, presentation = await self.document(uuid.UUID(p["documentId"]))
        await self.check_task_revision(task, state)
        slot = next((s for s in p["slots"] if not s.get("committed")), None)
        if slot:
            from services.agent_tools.workflow_export import check_asset_fingerprints
            check_asset_fingerprints(self.owner_id, slot.get("assetHashes", {}))
            before_pages = await self.page_fingerprints(state.id)
            await self._apply_tool(state, presentation, ToolRequest(tool="saveSlide", arguments={
                "index": slot["index"], "layoutId": slot["layoutId"],
                "content": json.dumps(slot["candidate"], ensure_ascii=False), "replaceOldSlideAtIndex": False,
            }))
            await self.advance_page_revisions(state.id, before_pages)
            state.revision += 1
            slot["committed"] = True
            slide = (await self.slides(state.id))[slot["index"]]
            slot["slideId"] = str(slide.id)
            from models.sql.agent_document import AgentPageRevision
            page_revision = await self.session.get(AgentPageRevision, slide.id)
            page_revision.revision = max(page_revision.revision, slot.get("pageRevision", 0))
            slot["pageRevision"] = page_revision.revision
            await self.add_receipt(slot["operationId"], state.id, slot["candidate"],
                                   {"status": "applied", "revision": state.revision, "slideId": str(slide.id)})
            p["stage"] = "saving"
        elif p["finish"]:
            await self._apply_tool(state, presentation, ToolRequest(tool="completeDocument"))
            state.revision += 1
            await self.add_receipt(p["finalizeOperationId"], state.id, {"finish": True}, {"revision": state.revision})
            state.workflow_task_id = None
            p["stage"] = "export_queued" if p["exportFormats"] else "completed"
            saved_assets = {url: value for saved in p["slots"] for url, value in saved.get("assetHashes", {}).items()}
            if p["exportFormats"]:
                p["deadline"] = await self.now() + DEADLINE_SECONDS
                from services.agent_tools.workflow_export import freeze_snapshot, check_asset_fingerprints, store_snapshot
                try:
                    for saved in p["slots"]:
                        check_asset_fingerprints(self.owner_id, saved.get("assetHashes", {}))
                    snapshot = await freeze_snapshot(self, state.id, saved_assets)
                    p["snapshotFile"] = store_snapshot(self.owner_id, task.id, snapshot)
                except HTTPException as exc:
                    # Saving is complete even if assets prevent export. Let the
                    # user read/fix that committed draft instead of trapping it.
                    p["stage"] = "export_input_error"
                    p["errors"] = [exc.detail if isinstance(exc.detail, dict) else {"code": "export_snapshot_failed"}]
                    task.status = AsyncTaskStatus.ERROR
            else:
                task.status = AsyncTaskStatus.COMPLETED
            next_ref = await self.new_ref("edit", {"revision": state.revision, "assetHashes": saved_assets}, state.id)
            p["nextEditRef"] = next_ref.id
        else:
            p["stage"] = "awaiting_finish"
            repair = await self.new_ref("repair", {"allowedSlots": []}, state.id, task.id)
            p["repairRef"] = repair.id
        p["revision"] = state.revision
        task.payload = copy.deepcopy(p)
        flag_modified(task, "payload")
        await self.session.commit()
        return self.status(task)

    @timed_stage
    async def read_for_edit(self, request):
        await self.begin()
        state, presentation = await self.document(request.document_id)
        if state.workflow_task_id or state.phase != "ready":
            fail(409, "generation_in_progress_use_task")
        snapshot = await self.read(state.id)
        if request.slide_ids is not None:
            requested = {str(slide_id) for slide_id in request.slide_ids}
            snapshot["slides"] = [slide for slide in snapshot["slides"] if slide["slideId"] in requested]
            if len(snapshot["slides"]) != len(requested):
                fail(404, "page_not_found")
        from services.agent_tools.workflow_export import asset_fingerprints
        asset_hashes = asset_fingerprints(self.owner_id, snapshot)
        ref = await self.new_ref("edit", {"revision": state.revision, "assetHashes": asset_hashes}, state.id)
        tools = await self._tools(presentation)
        from services.agent_tools.catalog import tool_catalog
        snapshot["editTools"] = [tool for tool in tool_catalog(tools) if tool["name"] in WRITE_RESULTS]
        snapshot["editRef"] = ref.id
        await self.session.commit()
        return snapshot

    async def revision_target(self, request):
        await self.begin()
        legacy = None
        if request.edit_ref:
            legacy = await self.ref(request.edit_ref, {"edit"})
            document_id, revision = legacy.document_id, legacy.data["revision"]
        else:
            document_id, revision = request.document_id, request.expected_revision
        state, _ = await self.document(document_id)
        return state, revision, legacy

    @staticmethod
    def check_edit_revision(state, revision):
        if state.workflow_task_id or state.phase != "ready":
            fail(409, "generation_in_progress_use_task")
        if state.revision != revision:
            raise HTTPException(409, {"code": "needs_rebase", "documentId": str(state.id),
                "expectedRevision": revision, "currentRevision": state.revision, "nextTool": "ppt_read",
                "message": "The document changed. Read current UI/notes and reapply only the intended changes."})

    @timed_stage
    async def edit(self, request):
        if request.page_edits:
            return await self.edit_pages(request)
        operations = []
        for operation in request.operations:
            if operation.tool not in WRITE_RESULTS:
                fail(422, "native_edit_tool_not_available")
            args = copy.deepcopy(operation.arguments)
            if operation.tool == "saveSlide" and isinstance(args.get("content"), dict):
                args["content"] = json.dumps(args["content"], ensure_ascii=False)
            operations.append(ToolRequest(tool=operation.tool, arguments=args))
        state, revision, legacy = await self.revision_target(request)
        # Read old receipts too, so an upgrade cannot repeat a successful edit.
        if legacy and legacy.request_hash:
            old_input = {"edit_ref": request.edit_ref,
                         "operations": [op.model_dump(mode="json") for op in request.operations]}
            if legacy.request_hash == digest(old_input):
                return await self.replay(legacy, old_input)
        payload = {"documentId": str(state.id), "expectedRevision": revision,
                   "operations": [op.model_dump(mode="json") for op in operations]}
        ref = await self.action_ref("edit_action", str(state.id), payload, state.id)
        replay = await self.replay(ref, payload)
        if replay:
            return replay
        self.check_edit_revision(state, revision)
        # Native changes, revision and the response receipt commit together.
        # No caller session, writer ownership or time-based authority exists.
        result = await self.mutate(state.id, None, BatchMutation(
            operationId=uuid.uuid5(uuid.NAMESPACE_URL, ref.id), expectedRevision=revision,
            operations=operations), commit=False)
        from services.agent_tools.workflow_export import asset_fingerprints
        asset_hashes = asset_fingerprints(self.owner_id, await self.read(state.id))
        next_ref = await self.new_ref("edit", {"revision": result["revision"], "assetHashes": asset_hashes}, state.id)
        return await self.accept(ref, payload, {"status": result["status"], "documentId": str(state.id),
            "revision": result["revision"], "editRef": next_ref.id, "result": result["result"]})

    async def edit_pages(self, request):
        await self.begin()
        state, _ = await self.document(request.document_id)
        payload = {"documentId": str(state.id),
                   "pageEdits": [page.model_dump(mode="json") for page in request.page_edits]}
        ref = await self.action_ref("page_edit_action", str(state.id), payload, state.id)
        replay = await self.replay(ref, payload)
        if replay:
            return replay
        self.check_edit_revision(state, state.revision)
        slides = {slide.id: slide for slide in await self.slides(state.id)}
        revisions = await self.page_revisions(state.id)
        operations = []
        page_tools = WRITE_RESULTS.keys() - OUTLINE_TOOLS - {"addNewSlide", "deleteSlide"}
        for page in request.page_edits:
            slide = slides.get(page.slide_id)
            if slide is None:
                fail(404, "page_not_found")
            if revisions.get(slide.id, 0) != page.expected_page_revision:
                raise HTTPException(409, {"code": "page_revision_conflict", "documentId": str(state.id),
                    "slideId": str(slide.id), "currentPageRevision": revisions.get(slide.id, 0),
                    "nextTool": "ppt_read", "message": "Only this page changed. Read this slideId and reapply the intended edit."})
            for operation in page.operations:
                if operation.tool not in page_tools:
                    fail(422, "page_edit_cannot_change_document_structure")
                args = copy.deepcopy(operation.arguments)
                # Stable identity wins over historical indices after insertions.
                args["index"] = slide.index
                if operation.tool == "saveSlide":
                    args["replaceOldSlideAtIndex"] = True
                    if isinstance(args.get("content"), dict):
                        args["content"] = json.dumps(args["content"], ensure_ascii=False)
                operations.append(ToolRequest(tool=operation.tool, arguments=args))
        result = await self.mutate(state.id, None, BatchMutation(
            operationId=uuid.uuid5(uuid.NAMESPACE_URL, ref.id), expectedRevision=state.revision,
            operations=operations), commit=False)
        latest = await self.page_revisions(state.id)
        return await self.accept(ref, payload, {"status": result["status"], "documentId": str(state.id),
            "revision": result["revision"], "pages": [{"slideId": str(page.slide_id),
                "pageRevision": latest.get(page.slide_id, 0)} for page in request.page_edits], "result": result["result"]})

    @timed_stage
    async def export(self, request):
        state, revision, legacy = await self.revision_target(request)
        if legacy and legacy.request_hash:
            old_input = {"edit_ref": request.edit_ref, "formats": request.formats}
            if legacy.request_hash == digest(old_input):
                return await self.replay(legacy, old_input)
        payload = {"documentId": str(state.id), "expectedRevision": revision,
                   "formats": sorted(set(request.formats))}
        ref = await self.action_ref("export_action", str(state.id), payload, state.id)
        replay = await self.replay(ref, payload)
        if replay:
            return replay
        self.check_edit_revision(state, revision)
        from services.agent_tools.workflow_export import freeze_snapshot, store_snapshot
        snapshot = await freeze_snapshot(self, state.id, legacy.data.get("assetHashes") if legacy else None)
        task = AsyncTaskModel(owner_id=self.owner_id, type=TASK_TYPE, status=AsyncTaskStatus.PENDING)
        task_ref = await self.new_ref("task", {}, state.id, task.id)
        snapshot_file = store_snapshot(self.owner_id, task.id, snapshot)
        task.payload = {"documentId": str(state.id), "taskRef": task_ref.id,
                        "actor": self.actor, "revision": state.revision, "stage": "export_queued",
                        "snapshotFile": snapshot_file, "exportFormats": payload["formats"],
                        "deadline": await self.now() + DEADLINE_SECONDS}
        ref.task_id = task.id
        self.session.add(task)
        return await self.accept(ref, payload, self.status(task))

    async def task(self, request):
        if request.action == "status":
            return await self.task_status(request.task_ref)
        ref = await self.ref(request.task_ref, {"task"}, actor=False)
        task = await self.load_task(ref.task_id)
        p = task.payload
        if request.action == "cancel" and p["stage"] not in {"completed", "cancelled"}:
            try:
                state, _ = await self.document(ref.document_id)
                if state.workflow_task_id == task.id:
                    state.workflow_task_id = None
                    state.phase = "cancelled"
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
            p["stage"] = "cancelled"
            p["claim"] = None
            task.status = AsyncTaskStatus.ERROR
        elif request.action == "resume":
            if p["stage"] in {"cancelled", "needs_rebase", "deleted", "expired", "export_input_error"}:
                fail(409, "task_requires_new_business_decision")
            if p["stage"] == "lease_lost":
                # Upgrade recovery for old records, without reinstating a lease.
                state, _ = await self.document(ref.document_id)
                await self.check_task_revision(task, state)
                p["stage"] = "queued" if any(s.get("candidate") for s in p.get("slots", [])) else "awaiting_content"
                p["errors"], p["nextTry"] = [], 0
                task.status = AsyncTaskStatus.PENDING
            # Reconnection changes audit identity, not authority. No takeover,
            # expiry wait or replacement editing token is needed.
            p["actor"] = self.actor
            if p["stage"] == "technical_error":
                p["stage"] = p.get("resumeStage", "queued")
                p["attempts"] = 0
                p["nextTry"] = 0
                task.status = AsyncTaskStatus.PENDING
        task.payload = copy.deepcopy(p)
        flag_modified(task, "payload")
        await self.session.commit()
        return self.status(task)
