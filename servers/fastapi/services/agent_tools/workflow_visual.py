"""Rendered evidence, delivery and Agent verdicts pinned to saved page versions."""
import asyncio
import copy
import hashlib
from pathlib import Path
import uuid

from sqlalchemy.orm.attributes import flag_modified

from services.agent_tools.service import digest, fail
from services.agent_tools.workflow_export import snapshot_directory, store_snapshot, render_error
from services.agent_tools.workflow_resources import freeze_resources
from services.agent_tools.generation_renderer import generation_renderers
from utils.get_env import get_app_data_directory_env

RENDERER_VERSION = "ppt-snapshot-v2"
ACTIVE = {"awaiting_content", "queued", "saving", "needs_input", "awaiting_finish"}


def theme_identity(presentation):
    return digest({"theme": presentation.theme, "fonts": presentation.fonts,
                   "assets": (presentation.layout or {}).get("_agentAssetHashes", {}),
                   "renderer": RENDERER_VERSION})


def pending_reviews(payload, presentation):
    identity = theme_identity(presentation)
    return [s.get("slideId") or s["slotId"] for s in payload["slots"]
            if not s.get("committed") or not (
                (r := payload.get("visualReviews", {}).get(s.get("slideId"), {})).get("verdict") == "pass"
                and r.get("pageRevision") == s["pageRevision"] and r.get("themeIdentity") == identity)]


async def target(service, task_ref):
    ref = await service.ref(task_ref, {"task"}, actor=False)
    task = await service.load_task(ref.task_id)
    state, presentation = await service.document(ref.document_id)
    await service.check_task_revision(task, state)
    if task.payload["stage"] not in ACTIVE or not task.payload.get("visualReviewRequired"):
        fail(409, "visual_task_not_active")
    return task, state, presentation


async def visual_read(service, request):
    results = []
    document_id = None
    for page in request.pages:
        task, state, presentation = await target(service, request.task_ref)
        slot = next((s for s in task.payload["slots"] if s.get("slideId") == str(page.slide_id)), None)
        if not slot or not slot.get("committed"):
            fail(409, "visual_page_not_saved")
        revisions = await service.page_revisions(state.id)
        if revisions.get(page.slide_id) != page.expected_page_revision:
            fail(409, "page_revision_conflict")
        identity = theme_identity(presentation)
        previous = task.payload.get("visualReviews", {}).get(str(page.slide_id), {})
        if previous.get("attempt", 0) >= 3 and previous.get("verdict") != "pass":
            fail(409, "visual_review_limit_reached")
        payload = {"slideId": str(page.slide_id), "pageRevision": page.expected_page_revision,
                   "themeIdentity": identity, "deliveryContext": request.delivery_context}
        evidence = await service.action_ref("visual_evidence", task.id, payload, state.id, task.id)
        if evidence.result:
            document_id = state.id
            results.append(evidence.result)
            await service.session.commit()
            continue
        evidence_id, task_id, document_id = evidence.id, task.id, state.id
        snapshot = await service.read(state.id)
        snapshot["slides"] = [s for s in snapshot["slides"] if s["slideId"] == str(page.slide_id)]
        snapshot["fonts"] = copy.deepcopy(presentation.fonts or {})
        tools = await service._tools(presentation)
        from services.agent_tools.catalog import tool_catalog, WRITE_RESULTS, OUTLINE_TOOLS
        tools = [tool for tool in tool_catalog(tools) if tool["name"] in WRITE_RESULTS
                 and tool["name"] not in OUTLINE_TOOLS | {"addNewSlide", "deleteSlide"}]
        assets = {**(presentation.layout or {}).get("_agentAssetHashes", {}), **slot.get("assetHashes", {})}
        # No resource IO, renderer wait or PNG read while SQLite's writer is reserved.
        await service.session.rollback()
        frozen = await asyncio.to_thread(freeze_resources, service.owner_id, task_id, snapshot, assets)
        fingerprint = await asyncio.to_thread(store_snapshot, service.owner_id, task_id, frozen)
        directory = snapshot_directory(service.owner_id, task_id).parent / "visual" / evidence_id
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        renderer = await generation_renderers.get(service.owner_id, task_id)
        result = await renderer.request({"type": "snapshot-export", "previewOnly": True,
            "snapshotPath": str(snapshot_directory(service.owner_id, task_id) / (fingerprint + ".json")),
            "snapshotHash": fingerprint, "formats": [], "outputDirectory": str(directory)})
        if result.get("error"):
            fail(503, render_error(result["error"])["code"])
        png = await asyncio.to_thread((directory / "preview-1.png").read_bytes)
        if len(png) > 5 * 1024 * 1024 or not png.startswith(b"\x89PNG\r\n\x1a\n"):
            fail(503, "visual_png_invalid")
        png_hash = hashlib.sha256(png).hexdigest()
        task, state, presentation = await target(service, request.task_ref)
        revisions = await service.page_revisions(state.id)
        if state.id != document_id or revisions.get(page.slide_id) != page.expected_page_revision or theme_identity(presentation) != identity:
            fail(409, "visual_evidence_stale")
        evidence = await service.action_ref("visual_evidence", task.id, payload, state.id, task.id)
        # Deterministic evidence identity also deduplicates concurrent same-version reads.
        if evidence.result:
            results.append(evidence.result)
            await service.session.commit()
            continue
        evidence.data = {**payload, "delivered": False}
        root = Path(get_app_data_directory_env()).resolve()
        url = "/app_data/" + (directory / "preview-1.png").resolve().relative_to(root).as_posix()
        evidence.result = {**payload, "evidenceId": evidence.id, "sha256": png_hash, "url": url,
                           "mimeType": "image/png", "ui": snapshot["slides"][0]["ui"], "editTools": tools,
                           "rendererVersion": RENDERER_VERSION}
        evidence.request_hash = digest(payload)
        results.append(evidence.result)
        await service.session.commit()
    return {"documentId": str(document_id), "images": results}


async def visual_ack(service, request):
    task, _, _ = await target(service, request.task_ref)
    for key in request.evidence_ids:
        evidence = await service.ref(key, {"visual_evidence"})
        if evidence.task_id != task.id or evidence.data.get("deliveryContext") != request.delivery_context:
            fail(404, "visual_evidence_not_found")
        evidence.data = {**evidence.data, "delivered": True}
    await service.session.commit()
    return {"acknowledged": True}


async def visual_review(service, request):
    task, state, presentation = await target(service, request.task_ref)
    evidence = await service.ref(request.evidence_id, {"visual_evidence"})
    data = evidence.data
    if evidence.task_id != task.id or data.get("deliveryContext") != request.delivery_context or not data.get("delivered"):
        fail(409, "visual_image_not_delivered")
    revisions = await service.page_revisions(state.id)
    if revisions.get(uuid.UUID(data["slideId"])) != data["pageRevision"] or theme_identity(presentation) != data["themeIdentity"]:
        fail(409, "visual_evidence_stale")
    report = {"verdict": request.verdict, "issues": request.issues, "summary": request.summary}
    if request.verdict == "pass" and request.issues:
        fail(422, "visual_pass_has_unresolved_issues")
    if data.get("report"):
        if data["report"] != report:
            fail(409, "visual_verdict_already_recorded")
        await queue_reviewed_finish(service, task, presentation)
        return {"documentId": str(state.id), "status": task.payload["stage"], "review": data["review"]}
    previous = task.payload.get("visualReviews", {}).get(data["slideId"], {})
    attempt = previous.get("attempt", 0) + 1
    if attempt > 3:
        fail(409, "visual_review_limit_reached")
    review = {**report, "pageRevision": data["pageRevision"], "themeIdentity": data["themeIdentity"],
              "evidenceId": evidence.id, "sha256": evidence.result["sha256"], "attempt": attempt}
    task.payload.setdefault("visualReviews", {})[data["slideId"]] = review
    task.payload["errors"] = [e for e in task.payload.get("errors", []) if e.get("code") != "visual_review_required"]
    evidence.data = {**data, "report": report, "review": review}
    flag_modified(task, "payload")
    await queue_reviewed_finish(service, task, presentation)
    await service.session.commit()
    return {"documentId": str(state.id), "status": task.payload["stage"], "review": review}


async def queue_reviewed_finish(service, task, presentation):
    # A prior finish request remains durable intent. Its idempotent receipt must
    # not leave a now-reviewed deck stuck forever in awaiting_finish.
    if task.payload.get("finish") and task.payload["stage"] == "awaiting_finish" and not pending_reviews(task.payload, presentation):
        task.payload["stage"] = "queued"
        flag_modified(task, "payload")
        await service.session.commit()
