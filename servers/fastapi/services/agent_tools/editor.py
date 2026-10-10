"""Native editor adaptation using the same ownership, transaction and versions as MCP."""
import copy
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from api.v1.auth.context import get_current_owner_id
from constants.presentation import MAX_NUMBER_OF_SLIDES
from models.sql.slide import SlideModel
from models.sql.agent_document import AgentOperationReceipt, AgentPageRevision
from services.agent_tools.assets import ExistingAssets, asset_references
from services.agent_tools.errors import OperationRejected
from services.agent_tools.service import AgentDocumentService, digest, fail
from utils.asset_directory_utils import normalize_slide_asset_url


def editor_service(session, presentation):
    owner = get_current_owner_id()
    if owner is None or presentation.owner_id != owner:
        fail(404, "document_not_found")
    return EditorService(session, owner)


class EditorService(AgentDocumentService):
    async def coordination(self, state, *, previous=None, slide_ids=None):
        versions = await self.page_revisions(state.id)
        return {
            "revision": state.revision,
            "previousRevision": previous,
            "pageRevisions": {str(key): value for key, value in versions.items()
                              if slide_ids is None or key in slide_ids},
            "writable": state.phase == "ready" and not state.workflow_task_id,
        }

    async def read_editor(self, document_id):
        state, presentation = await self.document(document_id)
        return presentation, await self.slides(state.id), await self.coordination(state)

    @staticmethod
    def conflict(code, **details):
        raise HTTPException(409, {
            "code": code, "retryable": False,
            "message": "This presentation changed elsewhere. Keep your unsaved changes and reload the current version before applying them.",
            **details,
        })

    async def save_editor(self, document_id, *, slide=None, expected_page_revision=None,
                          expected_revision=None, expected_pages=None, slides=None,
                          metadata=None):
        # SQLModel table request instances can retain JSON UUID strings.
        for row in ([slide] if slide is not None else (slides or [])):
            try:
                row.id = uuid.UUID(str(row.id))
                row.presentation = uuid.UUID(str(row.presentation))
            except (TypeError, ValueError, AttributeError):
                fail(422, "invalid_slide_identity")
        state, presentation = await self.document(document_id)
        stored = {row.id: row for row in await self.slides(state.id)}
        versions = await self.page_revisions(state.id)
        single = slide is not None
        if single and (slide.id not in stored or slide.presentation != state.id):
            fail(404, "page_not_found")
        payload = {
            "slide": slide.model_dump(mode="json") if single else None,
            "slides": [row.model_dump(mode="json") for row in slides] if slides is not None else None,
            "metadata": metadata, "expectedRevision": expected_revision,
            "expectedPageRevision": expected_page_revision, "expectedPages": expected_pages,
        }
        payload_hash = digest(payload)
        operation = uuid.uuid5(uuid.NAMESPACE_URL, f"native-editor:{state.id}:{payload_hash}")
        replay = await self.receipt(operation, payload_hash)
        if replay:
            receipt = replay["coordination"]
            current = {str(key): value for key, value in versions.items()}
            still_current = (current.get(str(slide.id)) == receipt["pageRevisions"].get(str(slide.id))
                             if single else state.revision == receipt["revision"])
            if not still_current:
                self.conflict("editor_receipt_superseded")
            return presentation, list(stored.values()), receipt
        if state.phase != "ready" or state.workflow_task_id:
            fail(409, "generation_in_progress_use_task")
        if single:
            if expected_page_revision is None:
                fail(428, "page_revision_required")
            if expected_page_revision != versions.get(slide.id, 0):
                self.conflict("page_revision_conflict", slideId=str(slide.id),
                              currentPageRevision=versions.get(slide.id, 0))
        else:
            if expected_revision is None:
                fail(428, "document_revision_required")
            if expected_revision != state.revision:
                self.conflict("needs_rebase", currentRevision=state.revision)
            if slides is not None:
                if not 1 <= len(slides) <= MAX_NUMBER_OF_SLIDES:
                    fail(422, "invalid_slide_count")
                ids = [row.id for row in slides]
                if len(set(ids)) != len(ids) or any(row.presentation != state.id for row in slides):
                    fail(422, "invalid_slide_identity")
                if expected_pages != {str(key): versions.get(key, 0) for key in stored}:
                    self.conflict("page_revision_conflict")

        previous = state.revision
        before = await self._fingerprint(state, presentation)
        before_pages = await self.page_fingerprints(state.id)
        assets = await ExistingAssets.for_document(
            self.session, self.owner_id, [presentation.layout, presentation.theme])
        incoming = [slide] if single else (slides or [])
        for row in incoming:
            try:
                await assets.validate([row.content, row.ui, row.properties])
            except OperationRejected as exc:
                fail(422, exc.code)
            # Native TemplateV2 editing stores UI. Do not admit generated HTML here.
            if row.html_content:
                fail(422, "managed_editor_requires_native_ui")
        if metadata is not None:
            # Font uploads are shared, administrator-managed native assets.
            for reference in asset_references(metadata):
                if (isinstance(reference, str) and reference.startswith(("/app_data/fonts/", "/vendor/fonts/"))
                        and assets._exists(reference, self.owner_id)):
                    assets.allowed.add(normalize_slide_asset_url(reference))
            try:
                await assets.validate(metadata)
            except OperationRejected as exc:
                fail(422, exc.code)
        async with self.session.begin_nested():
            retired = {}
            if single:
                stored[slide.id].sqlmodel_update(slide.model_dump(
                    exclude={"id", "owner_id", "presentation", "index"}))
            elif slides is not None:
                old_outlines = (presentation.outlines or {}).get("slides", [])
                old_structure = (presentation.structure or {}).get("slides", [])
                outlines, structure = [], []
                for index, row in enumerate(slides):
                    existing = stored.get(row.id)
                    old_index = existing.index if existing is not None else -1
                    outlines.append(copy.deepcopy(old_outlines[old_index])
                                    if 0 <= old_index < len(old_outlines)
                                    else {"content": f"# Slide {index + 1}"})
                    structure.append(old_structure[old_index]
                                     if 0 <= old_index < len(old_structure) else 0)
                    values = row.model_dump(exclude={"id", "owner_id", "presentation", "index"})
                    if existing is None:
                        existing = SlideModel(id=row.id, presentation=state.id,
                                              owner_id=self.owner_id, index=index, **values)
                        self.session.add(existing)
                    else:
                        existing.sqlmodel_update(values)
                        existing.index = index
                for key in stored.keys() - {row.id for row in slides}:
                    retired[str(key)] = versions.get(key, 0)
                    await self.session.delete(stored[key])
                presentation.n_slides = len(slides)
                presentation.outlines = {**(presentation.outlines or {}), "slides": outlines}
                presentation.structure = {**(presentation.structure or {}), "slides": structure}
                new_ids = {row.id for row in slides} - stored.keys()
                if new_ids:
                    # Native undo can restore a deleted UUID. Keep its version
                    # above every prior incarnation so stale page edits still fail.
                    history = await self.session.scalars(select(AgentOperationReceipt.result).where(
                        AgentOperationReceipt.document_id == state.id,
                        AgentOperationReceipt.owner_id == self.owner_id,
                        AgentOperationReceipt.result["retiredPageRevisions"].as_string().is_not(None)))
                    floors = {}
                    for receipt in history:
                        for key, value in receipt.get("retiredPageRevisions", {}).items():
                            floors[key] = max(floors.get(key, 0), value)
                    await self.session.flush()
                    for key in new_ids:
                        if str(key) in floors:
                            self.session.add(AgentPageRevision(slide_id=key, document_id=state.id,
                                                              owner_id=self.owner_id, revision=floors[str(key)]))
            for key, value in (metadata or {}).items():
                if key not in {"title", "theme"}:
                    fail(422, "unsupported_editor_metadata")
                setattr(presentation, key, value)
            await self.advance_revisions(state, presentation, before, before_pages)
            coordination = await self.coordination(state, previous=previous,
                                                   slide_ids={slide.id} if single else None)
            await self._record(operation, state.id, payload_hash,
                               {"coordination": coordination, **({"retiredPageRevisions": retired} if retired else {})},
                               commit=False)
        await self.session.commit()
        return presentation, await self.slides(state.id), coordination
