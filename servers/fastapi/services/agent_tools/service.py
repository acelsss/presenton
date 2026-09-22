"""Persistent native-tool executor. Every write, revision and receipt share one transaction."""

import copy
import hashlib
import json
import secrets
import uuid
from datetime import timezone

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from models.sql.agent_document import (
    AgentCallerSession,
    AgentDocument,
    AgentPageRevision,
    AgentOperationReceipt,
)
from models.sql.presentation import PresentationModel, PresentationVersion
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from services.agent_tools.assets import ExistingAssets
from services.agent_tools.errors import OperationRejected
from services.agent_tools.schemas import BatchMutation
from services.agent_tools.catalog import (
    LIFECYCLE_TOOLS,
    OUTLINE_TOOLS,
    READ_TOOLS,
    WRITE_RESULTS,
)
from services.chat.execution_policy import ChatExecutionPolicy, project_slide_ui
from services.chat.memory_layer import PresentationChatMemoryLayer
from services.chat.tools import ChatTools


SESSION_SECONDS = 24 * 60 * 60


def fail(status: int, code: str):
    raise HTTPException(status_code=status, detail={"code": code})


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


class Rejected(Exception):
    def __init__(self, result):
        self.result = result


class AgentDocumentService:
    def __init__(self, session, owner_id: uuid.UUID):
        if owner_id is None:
            fail(401, "authentication_required")
        self.session = session
        self.owner_id = owner_id
        # Trusted server-internal context; never populated from a request field.
        self.session.info["agent_tools"] = True
        self.session.info["agent_owner_id"] = owner_id

    async def now(self) -> int:
        await self._begin_sqlite_transaction()
        clock = (
            func.clock_timestamp()
            if self.session.bind.dialect.name == "postgresql"
            else func.current_timestamp()
        )
        value = await self.session.scalar(select(clock))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp())

    async def _begin_sqlite_transaction(self):
        if self.session.bind.dialect.name == "sqlite":
            connection = await self.session.connection()
            in_transaction = await connection.run_sync(
                lambda conn: conn.connection.driver_connection.in_transaction
            )
            if not in_transaction:
                # sqlite3 legacy mode does not BEGIN for SELECT/SAVEPOINT. Without
                # this, releasing a native-tool savepoint could commit prematurely.
                await connection.exec_driver_sql("BEGIN IMMEDIATE")

    async def open_session(self, label: str):
        token = secrets.token_urlsafe(32)
        caller = AgentCallerSession(
            owner_id=self.owner_id,
            label=label,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=await self.now() + SESSION_SECONDS,
        )
        self.session.add(caller)
        await self.session.commit()
        return {
            "sessionId": str(caller.id),
            "sessionToken": token,
            "expiresAt": caller.expires_at,
        }

    async def caller(self, token: str) -> AgentCallerSession:
        caller = await self.session.scalar(
            select(AgentCallerSession).where(
                AgentCallerSession.owner_id == self.owner_id,
                AgentCallerSession.token_hash
                == hashlib.sha256(token.encode()).hexdigest(),
            )
        )
        if caller is None or caller.expires_at <= await self.now():
            fail(401, "caller_session_expired_or_invalid")
        return caller

    async def document(self, document_id: uuid.UUID):
        await self._begin_sqlite_transaction()
        state = await self.session.scalar(
            select(AgentDocument)
            .where(
                AgentDocument.id == document_id,
                AgentDocument.owner_id == self.owner_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if state is None:
            fail(404, "document_not_found")
        presentation = await self.session.scalar(
            select(PresentationModel)
            .where(
                PresentationModel.id == state.id,
                PresentationModel.owner_id == self.owner_id,
            )
            .execution_options(populate_existing=True)
        )
        if presentation is None:
            fail(404, "document_not_found")
        return state, presentation

    async def slides(self, document_id):
        return list(
            await self.session.scalars(
                select(SlideModel)
                .where(
                    SlideModel.presentation == document_id,
                    SlideModel.owner_id == self.owner_id,
                )
                .order_by(SlideModel.index)
                .execution_options(populate_existing=True)
            )
        )

    async def receipt(self, operation_id, payload_hash=None):
        receipt = await self.session.scalar(
            select(AgentOperationReceipt).where(
                AgentOperationReceipt.owner_id == self.owner_id,
                AgentOperationReceipt.operation_id == operation_id,
            )
        )
        if receipt is None:
            return None
        if payload_hash is not None and payload_hash != receipt.payload_hash:
            fail(409, "operation_id_reused")
        return {**receipt.result, "replayed": True}

    async def _record(self, operation_id, document_id, payload_hash, result, *, commit=True):
        self.session.add(
            AgentOperationReceipt(
                owner_id=self.owner_id,
                operation_id=operation_id,
                document_id=document_id,
                payload_hash=payload_hash,
                result=result,
                created_at=await self.now(),
            )
        )
        if not commit:
            await self.session.flush()
            return result
        try:
            await self.session.commit()
        except IntegrityError:
            # Concurrent creates or cross-document reuse: uniqueness decides the winner.
            await self.session.rollback()
            existing = await self.receipt(operation_id, payload_hash)
            if existing is None:
                raise
            return existing
        return result

    async def create(self, request):
        payload_hash = digest(
            {"create": request.model_dump(mode="json", exclude={"operation_id"})}
        )
        existing = await self.receipt(request.operation_id, payload_hash)
        if existing is not None:
            return existing
        state, presentation = await self.stage_document(request.title, request.language)
        return await self._record(
            request.operation_id, presentation.id, payload_hash,
            {"status": "applied", "documentId": str(presentation.id), "revision": 0,
             "phase": "outline", "operationId": str(request.operation_id)},
        )

    async def stage_document(self, title, language):
        """Reuse native creation inside a caller-owned transaction; never commit."""
        presentation = PresentationModel(
            owner_id=self.owner_id,
            agent_managed=True,
            version=PresentationVersion.V2_STANDARD,
            content="",
            n_slides=0,
            language=language,
            title=title,
            outlines={"slides": []},
        )
        self.session.add(presentation)
        await self.session.flush()
        state = AgentDocument(id=presentation.id, owner_id=self.owner_id)
        self.session.add(state)
        return state, presentation

    async def read(self, document_id):
        state, presentation = await self.document(document_id)
        slides = await self.slides(document_id)
        page_revisions = await self.page_revisions(document_id)
        return {
            "documentId": str(state.id),
            "revision": state.revision,
            "phase": state.phase,
            "title": presentation.title,
            "outlines": presentation.outlines,
            "templateId": state.template_id,
            "theme": presentation.theme,
            "slides": [
                {
                    "slideId": str(slide.id),
                    "pageRevision": page_revisions.get(slide.id, 0),
                    "index": slide.index,
                    "layoutId": slide.layout,
                    "ui": slide.ui,
                    "projection": project_slide_ui(slide.ui),
                    "speakerNote": slide.speaker_note,
                }
                for slide in slides
            ],
        }

    async def page_revisions(self, document_id):
        rows = await self.session.scalars(select(AgentPageRevision).where(
            AgentPageRevision.document_id == document_id, AgentPageRevision.owner_id == self.owner_id
        ).execution_options(populate_existing=True))
        return {row.slide_id: row.revision for row in rows}

    async def page_fingerprints(self, document_id):
        # Index is intentionally excluded: insertion/reordering must not make a
        # stable slide ID's unchanged content conflict with another page's edit.
        return {slide.id: digest({"ui": slide.ui, "content": slide.content,
            "notes": slide.speaker_note, "layout": slide.layout, "layoutGroup": slide.layout_group,
            "properties": slide.properties, "html": slide.html_content})
            for slide in await self.slides(document_id)}

    async def advance_page_revisions(self, document_id, before):
        for slide_id, value in (await self.page_fingerprints(document_id)).items():
            if before.get(slide_id) == value:
                continue
            row = await self.session.get(AgentPageRevision, slide_id)
            if row is None:
                row = AgentPageRevision(slide_id=slide_id, document_id=document_id, owner_id=self.owner_id)
                self.session.add(row)
            elif row.owner_id != self.owner_id or row.document_id != document_id:
                fail(404, "page_not_found")
            row.revision += 1
        await self.session.flush()

    async def advance_revisions(self, state, presentation, before, before_pages):
        """Shared native-editor/MCP revision update inside the caller's transaction."""
        await self.session.flush()
        changed = (before != await self._fingerprint(state, presentation)
                   or before_pages != await self.page_fingerprints(state.id))
        if changed:
            await self.advance_page_revisions(state.id, before_pages)
            state.revision += 1
        return changed

    async def _tools(self, presentation):
        assets = await ExistingAssets.for_document(
            self.session, self.owner_id, presentation.layout
        )
        memory = PresentationChatMemoryLayer(
            self.session,
            presentation.id,
            execution_policy=ChatExecutionPolicy(
                managed_transaction=True,
                allow_generation=False,
                use_memory=False,
                current_ui_reads=True,
                preserve_slide_ids=True,
                preserve_ui_metadata=True,
                soft_text_lengths=True,
                validate_assets=assets.validate,
            ),
        )
        return ChatTools(memory, strict_indices=True)

    async def read_tool(self, document_id, request):
        if request.tool not in READ_TOOLS:
            fail(422, "read_tool_not_available")
        state, presentation = await self.document(document_id)
        tools = await self._tools(presentation)
        try:
            result = await tools.execute_validated(request.tool, request.arguments)
        except ValueError:
            fail(422, "invalid_tool_arguments")
        return {
            "documentId": str(state.id),
            "revision": state.revision,
            "result": result,
        }

    async def _fingerprint(self, state, presentation):
        slides = await self.slides(state.id)
        return digest(
            {
                "phase": state.phase,
                "templateId": state.template_id,
                "title": presentation.title,
                "outlines": presentation.outlines,
                "layout": presentation.layout,
                "theme": presentation.theme,
                "n_slides": presentation.n_slides,
                "slides": [
                    {
                        "id": str(s.id),
                        "index": s.index,
                        "layout": s.layout,
                        "ui": s.ui,
                        "content": s.content,
                        "notes": s.speaker_note,
                    }
                    for s in slides
                ],
            }
        )

    async def read_tools(self, document_id, request):
        # read_tool holds the document lock until this request's session closes.
        # Reuse its allowlist, validation and native handlers for one revision.
        results = []
        for operation in request.operations:
            response = await self.read_tool(document_id, operation)
            results.append({"tool": operation.tool, "result": response["result"]})
        return {
            "documentId": response["documentId"],
            "revision": response["revision"],
            "results": results,
        }

    async def _lifecycle(self, name, arguments, state, presentation):
        schema = LIFECYCLE_TOOLS[name][0]
        payload = schema.model_validate(arguments)
        if name == "confirmOutline":
            if state.phase != "outline" or not (presentation.outlines or {}).get(
                "slides"
            ):
                raise OperationRejected("nonempty_outline_required")
            state.phase = "outline_confirmed"
        elif name == "selectTemplate":
            if state.phase != "outline_confirmed":
                raise OperationRejected("outline_confirmation_required")
            template = await self.session.scalar(
                select(TemplateV2).where(
                    TemplateV2.id == payload.template_id,
                    or_(
                        TemplateV2.owner_id == self.owner_id,
                        (
                            TemplateV2.owner_id.is_(None)
                            & TemplateV2.is_default.is_(True)
                        ),
                    ),
                )
            )
            if template is None or not (template.layouts or {}).get("layouts"):
                raise OperationRejected("template_missing_or_forbidden")
            # Snapshot raw V2 layouts, never follow mutable template rows for later saves.
            presentation.layout = {
                **copy.deepcopy(template.layouts),
                "name": f"custom-{template.id}",
            }
            presentation.theme = copy.deepcopy(template.theme)
            state.template_id = template.id
            state.phase = "composing"
        elif name == "completeDocument":
            slides = await self.slides(state.id)
            if (
                state.phase != "composing"
                or len(slides) != len(presentation.outlines["slides"])
                or any(
                    not slide.ui or slide.layout == "__blank_slide__"
                    for slide in slides
                )
            ):
                raise OperationRejected("all_outline_pages_must_be_saved")
            state.phase = "ready"
        return {"phase": state.phase}

    async def _validate_index(self, request, state, presentation):
        index = request.arguments.get("index")
        if index is None:
            return  # Native schema decides whether omission/null is valid.
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise OperationRejected("invalid_index")
        if request.tool in OUTLINE_TOOLS:
            count = len((presentation.outlines or {}).get("slides", []))
            inserts = request.tool == "addOutline"
        else:
            count = len(await self.slides(state.id))
            inserts = request.tool == "addNewSlide" or (
                request.tool == "saveSlide"
                and not request.arguments.get(
                    "replaceOldSlideAtIndex",
                    request.arguments.get("replace_old_slide_at_index", False),
                )
            )
            if (
                inserts
                and state.phase == "composing"
                and count >= len(presentation.outlines["slides"])
            ):
                raise OperationRejected("outline_page_count_exceeded")
        if index > count or (not inserts and index == count):
            raise OperationRejected("index_out_of_range")

    async def _apply_tool(self, state, presentation, request):
        if request.tool in LIFECYCLE_TOOLS:
            return await self._lifecycle(
                request.tool, request.arguments, state, presentation
            )
        if request.tool in OUTLINE_TOOLS:
            if state.phase != "outline":
                raise OperationRejected("outline_already_confirmed")
        elif state.phase not in {"composing", "ready"}:
            raise OperationRejected("template_selection_required")
        if state.phase == "composing" and request.tool in {"addNewSlide", "deleteSlide"}:
            raise OperationRejected("save_outline_pages_before_structural_editing")
        tools = await self._tools(presentation)
        await self._validate_index(request, state, presentation)
        result = await tools.execute_validated(request.tool, request.arguments)
        if result.get(WRITE_RESULTS[request.tool]) is not True:
            raise Rejected(result)
        if state.phase == "ready":
            presentation.n_slides = len(await self.slides(state.id))
        return result

    async def mutate(self, document_id, caller, request, *, commit=True):
        await self._begin_sqlite_transaction()
        batch = isinstance(request, BatchMutation)
        operations = request.operations if batch else [request]
        payload = (
            {"operations": [op.model_dump(mode="json") for op in operations]}
            if batch
            else {"tool": request.tool, "arguments": request.arguments}
        )
        payload_hash = digest(
            {
                "documentId": str(document_id),
                **payload,
                "expectedRevision": request.expected_revision,
            }
        )
        existing = await self.receipt(request.operation_id, payload_hash)
        if existing is not None:
            return existing
        state, presentation = await self.document(document_id)
        # A request may have waited for another transaction which wrote this receipt.
        existing = await self.receipt(request.operation_id, payload_hash)
        if existing is not None:
            return existing
        if state.workflow_task_id:
            fail(409, "workflow_in_progress")
        if state.revision != request.expected_revision:
            fail(409, "revision_conflict")
        if any(
            op.tool not in WRITE_RESULTS and op.tool not in LIFECYCLE_TOOLS
            for op in operations
        ):
            fail(422, "write_tool_not_available")
        revision = state.revision
        operation_index = 0
        try:
            # Native rejection rolls back ALL handler changes, while its receipt persists.
            async with self.session.begin_nested():
                before = await self._fingerprint(state, presentation)
                before_pages = await self.page_fingerprints(state.id)
                results = []
                for operation_index, operation in enumerate(operations):
                    result = await self._apply_tool(state, presentation, operation)
                    await self.session.flush()
                    results.append({"tool": operation.tool, "result": result})
                if batch:
                    result = {"results": results}
                await self.session.flush()
                changed = await self.advance_revisions(state, presentation, before, before_pages)
                revision = state.revision
                status = "applied" if changed else "noop"
        except Rejected as exc:
            result, status = exc.result, "rejected"
        except OperationRejected as exc:
            result, status = {"code": exc.code}, "rejected"
        except ValidationError as exc:
            result, status = (
                {
                    "code": "invalid_tool_arguments",
                    "fields": [
                        {
                            "path": ".".join(map(str, error["loc"])),
                            "type": error["type"],
                        }
                        for error in exc.errors(
                            include_input=False,
                            include_context=False,
                            include_url=False,
                        )[:10]
                    ],
                },
                "rejected",
            )
        except ValueError:
            result, status = {"code": "invalid_tool_arguments"}, "rejected"
        if batch and status == "rejected":
            result = {"failedIndex": operation_index, "error": result}
        response = {
            "documentId": str(document_id),
            "operationId": str(request.operation_id),
            "status": status,
            "revision": revision,
            "result": result,
        }
        return await self._record(
            request.operation_id, document_id, payload_hash, response, commit=commit
        )

    async def templates(self):
        templates = await self.session.scalars(
            select(TemplateV2)
            .where(
                or_(
                    TemplateV2.owner_id == self.owner_id,
                    (TemplateV2.owner_id.is_(None) & TemplateV2.is_default.is_(True)),
                )
            )
            .order_by(TemplateV2.id)
        )
        return [
            {"templateId": t.id, "name": t.name, "description": t.description}
            for t in templates
            if (t.layouts or {}).get("layouts")
        ]
