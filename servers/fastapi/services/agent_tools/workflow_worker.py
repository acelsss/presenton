"""Restart-safe execution using database locks and bounded export claims."""
import asyncio
import copy
import logging
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from enums.async_task_status import AsyncTaskStatus
from models.sql.async_task import AsyncTaskModel
from services.agent_tools.errors import OperationRejected
from services.agent_tools.service import Rejected
from services.agent_tools.workflow import TASK_TYPE, WorkflowService
from services.agent_tools.workflow_export import render_snapshot, render_error, load_snapshot

LOGGER = logging.getLogger(__name__)


async def record_failure(sessions, owner_id, task_id, exc, *, cursor=None, claim=None):
    async with sessions() as session:
        service = WorkflowService(session, owner_id, "worker")
        task = await service.load_task(task_id)
        p = task.payload
        if cursor is not None and cursor != (p["revision"], p["stage"]):
            return  # Another worker already advanced this transaction's cursor.
        if claim is not None and (p.get("claim") or {}).get("id") != claim:
            return  # A stale renderer cannot change a newer worker's outcome.
        service.actor = p["actor"]
        if p["stage"] in {"cancelled", "completed"}:
            return
        detail = exc.detail if isinstance(exc, HTTPException) else {}
        code = detail.get("code") if isinstance(detail, dict) else None
        p["errors"] = [{"code": code or "execution_failed", "type": type(exc).__name__}]
        if code in {"snapshot_render_timeout", "snapshot_render_failed"}:
            p["errors"][0].update(render_error(detail))
        if isinstance(exc, (OperationRejected, Rejected)):
            slot = next((s for s in p.get("slots", []) if not s.get("committed")), None)
            if slot:
                repair = await service.new_ref("repair", {"allowedSlots": [slot["slotId"]]}, uuid.UUID(p["documentId"]), task.id)
                p["repairRef"], p["stage"] = repair.id, "needs_input"
                p["errors"] = [{"slotId": slot["slotId"], "code": getattr(exc, "code", "native_validation_failed")}]
            else:
                p["stage"] = "technical_error"
        elif isinstance(exc, HTTPException) and exc.status_code < 500:
            p["stage"] = {"needs_rebase": "needs_rebase", "task_no_longer_current": "cancelled",
                          "document_not_found": "deleted", "workflow_deadline_exceeded": "expired",
                          "export_snapshot_invalid": "export_input_error", "export_snapshot_missing": "export_input_error",
                          "export_snapshot_changed": "export_input_error"}.get(code, "technical_error")
            task.status = AsyncTaskStatus.ERROR
        else:
            p["attempts"] = p.get("attempts", 0) + 1
            p["resumeStage"] = "export_queued" if p["stage"] in {"export_queued", "exporting"} else "queued"
            p["stage"] = p["resumeStage"] if p["attempts"] < 3 else "technical_error"
            p["nextTry"] = await service.now() + 2 ** p["attempts"]
            p["claim"] = None
            if p["attempts"] >= 3:
                task.status = AsyncTaskStatus.ERROR
        task.payload = copy.deepcopy(p)
        flag_modified(task, "payload")
        await session.commit()
        # No candidate text, assets, tokens or exception body in logs.
        LOGGER.warning("[ppt_workflow] task=%s stage=%s error_type=%s code=%s phase=%s",
                       task_id, p["stage"], type(exc).__name__, p["errors"][0]["code"],
                       p["errors"][0].get("phase", "-"))


async def drive(sessions, owner_id, task_id, *, budget_seconds=2):
    """Small save operations can finish inside submit; export is handled by worker."""
    deadline = time.monotonic() + budget_seconds
    result = None
    while time.monotonic() < deadline:
        service = None
        try:
            async with sessions() as session:
                service = WorkflowService(session, owner_id, "worker")
                result = await service.step(task_id)
            if result["status"] not in {"queued", "saving"} or (result.get("retryAt") or 0) > time.time():
                break
        except Exception as exc:
            cursor = service.session.info.get("ppt_step_cursor") if service else None
            await record_failure(sessions, owner_id, task_id, exc, cursor=cursor)
            break
    return result


async def export_one(sessions, owner_id, task_id):
    claim_id = uuid.uuid4().hex
    async with sessions() as session:
        service = WorkflowService(session, owner_id, "worker")
        task = await service.load_task(task_id)
        p = task.payload
        now = await service.now()
        if p["stage"] not in {"export_queued", "exporting"} or p.get("nextTry", 0) > now:
            return
        if p.get("claim") and p["claim"]["until"] > now:
            return
        await service.document(uuid.UUID(p["documentId"]))
        if p["deadline"] <= now:
            from services.agent_tools.service import fail
            fail(409, "workflow_deadline_exceeded")
        p["stage"], p["claim"] = "exporting", {"id": claim_id, "until": now + 360}
        # Keep read compatibility with tasks queued before file-backed snapshots.
        snapshot, snapshot_file = p.get("snapshot"), p.get("snapshotFile")
        formats = [format for format in p["exportFormats"] if p.get("exports", {}).get(format, {}).get("status") != "completed"]
        task.payload = copy.deepcopy(p)
        flag_modified(task, "payload")
        await session.commit()
    try:
        if snapshot_file:
            snapshot = load_snapshot(owner_id, task_id, snapshot_file)
        result = await render_snapshot(owner_id, task_id, claim_id, snapshot, formats)
    except Exception as exc:
        await record_failure(sessions, owner_id, task_id, exc, claim=claim_id)
        return
    async with sessions() as session:
        service = WorkflowService(session, owner_id, "worker")
        task = await service.load_task(task_id)
        p = task.payload
        if p["stage"] != "exporting" or (p.get("claim") or {}).get("id") != claim_id:
            return  # Cancellation or a newer claim fences publication.
        await service.document(uuid.UUID(p["documentId"]))
        p["exports"] = {**p.get("exports", {}), **result["exports"]}
        p["previews"] = result["previews"]
        p["claim"] = None
        succeeded = all(item["status"] == "completed" for item in p["exports"].values())
        p["errors"] = [{"format": format, **render_error(item)} for format, item in p["exports"].items()
                       if item["status"] != "completed"]
        if succeeded:
            p["attempts"], p["nextTry"] = 0, 0
        p["stage"] = "completed" if succeeded else "technical_error"
        p["resumeStage"] = "export_queued"
        task.status = AsyncTaskStatus.COMPLETED if succeeded else AsyncTaskStatus.ERROR
        task.payload = copy.deepcopy(p)
        flag_modified(task, "payload")
        await session.commit()


async def sweep(sessions):
    async with sessions() as session:
        # Do not scan full candidate/snapshot JSON, or let idle drafts starve work.
        stage_column = AsyncTaskModel.payload["stage"].as_string()
        rows = list((await session.execute(select(AsyncTaskModel.id, AsyncTaskModel.owner_id, stage_column).where(
            AsyncTaskModel.type == TASK_TYPE, AsyncTaskModel.status == AsyncTaskStatus.PENDING,
            stage_column.in_(["queued", "saving", "export_queued", "exporting"]),
        ).order_by(AsyncTaskModel.created_at).limit(100))).all())
    for task_id, owner_id, stage in rows:
        try:
            if stage in {"queued", "saving"}:
                await drive(sessions, owner_id, task_id)
            elif stage in {"export_queued", "exporting"}:
                await export_one(sessions, owner_id, task_id)
        except Exception as exc:
            await record_failure(sessions, owner_id, task_id, exc)


async def worker_loop(sessions, stop):
    while not stop.is_set():
        try:
            await sweep(sessions)
        except Exception as exc:
            LOGGER.warning("[ppt_workflow] scan_failed error_type=%s", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass
