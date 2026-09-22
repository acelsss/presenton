import asyncio
import copy
import json
import os
import uuid
import zipfile
from pathlib import Path

import pytest

from models.sql.ppt_workflow import PptWorkflowRef
from services.agent_tools import workflow_schemas as inputs
from services.agent_tools.workflow_worker import export_one, sweep
from tests.unit.test_agent_tools import database
from tests.unit.test_ppt_workflow import WorkflowClient, start, complete


def test_pinned_dependency_change_is_rejected(tmp_path, monkeypatch):
    from services.agent_tools.workflow_export import asset_fingerprints, check_asset_fingerprints
    from fastapi import HTTPException
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    owner = uuid.uuid4()
    asset = tmp_path / "images" / "users" / str(owner) / "asset.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"version one")
    url = f"/app_data/images/users/{owner}/asset.png"
    manifest = asset_fingerprints(owner, {"type": "image", "data": url})
    check_asset_fingerprints(owner, manifest)
    asset.write_bytes(b"version two")
    with pytest.raises(HTTPException, match="pinned_asset_changed"):
        check_asset_fingerprints(owner, manifest)


async def queue_export(client):
    _, result = await start(client, 2)
    await complete(client, result)
    read = await client.call("read_for_edit", inputs.Read(documentId=result["documentId"]))
    request = inputs.Export(editRef=read["editRef"], formats=["pptx", "pdf"])
    queued = await client.call("export", request)
    assert (await client.call("export", request))["taskRef"] == queued["taskRef"]
    async with client.sessions() as session:
        from models.sql.async_task import AsyncTaskModel
        ref = await session.get(PptWorkflowRef, queued["taskRef"])
        task_id = ref.task_id
        task = await session.get(AsyncTaskModel, task_id)
        assert "snapshot" not in task.payload and len(task.payload["snapshotFile"]) == 64
        assert len(json.dumps(task.payload)) < 2000
    return result, queued, task_id


def test_file_snapshot_is_immutable_owner_scoped_and_not_a_caller_path(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from services.agent_tools.workflow_export import store_snapshot, load_snapshot, snapshot_directory
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    owner = uuid.uuid4()
    snapshot = {"revision": 8, "asset": "x" * 2_000_000}
    fingerprint = store_snapshot(owner, "task-test", snapshot)
    assert load_snapshot(owner, "task-test", fingerprint) == snapshot
    assert not list(tmp_path.rglob("*.tmp"))
    with pytest.raises(HTTPException, match="export_snapshot_missing"):
        load_snapshot(uuid.uuid4(), "task-test", fingerprint)
    with pytest.raises(HTTPException, match="export_snapshot_invalid"):
        load_snapshot(owner, "task-test", "../snapshot")
    (snapshot_directory(owner, "task-test") / f"{fingerprint}.json").write_text('{}')
    with pytest.raises(HTTPException, match="export_snapshot_changed"):
        load_snapshot(owner, "task-test", fingerprint)


def test_export_claim_is_exclusive_snapshot_revision_is_immutable_and_cancel_fences_publication(tmp_path, monkeypatch):
    import services.agent_tools.workflow_worker as worker
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            result, queued, task_id = await queue_export(client)
            entered, finish = asyncio.Event(), asyncio.Event()
            calls = []
            async def render(owner, task, claim, snapshot, formats):
                calls.append(copy.deepcopy(snapshot))
                entered.set()
                await finish.wait()
                return {"exports": {"pptx": {"status": "completed", "url": "test"}}, "previews": []}
            monkeypatch.setattr(worker, "render_snapshot", render)
            first = asyncio.create_task(export_one(sessions, client.owner, task_id))
            await entered.wait()
            await export_one(sessions, client.owner, task_id)
            assert len(calls) == 1
            read = await client.call("read_for_edit", inputs.Read(documentId=result["documentId"]))
            await client.call("edit", inputs.Edit(editRef=read["editRef"], operations=[{"tool": "updateElement", "arguments": {
                "index": 0, "elementPath": "components[0].elements[0]", "text": "After snapshot"}}]))
            assert "After snapshot" not in json.dumps(calls)
            cancelled = await client.call("task", inputs.Task(taskRef=queued["taskRef"], action="cancel"))
            finish.set()
            await first
            after = await client.call("task", inputs.Task(taskRef=queued["taskRef"]))
            assert after["status"] == "cancelled" and after["exports"] == {}
    asyncio.run(run())


def test_restart_scan_preserves_durable_work_and_resumes_pages(tmp_path):
    from services.async_tasks import fail_interrupted_async_tasks
    from tests.unit.test_ppt_workflow import pages
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, result = await start(client, 2)
            await client.call("submit", inputs.Submit(editRef=result["editRef"], pages=pages(result)))
            async with sessions() as session:
                from models.sql.async_task import AsyncTaskModel
                from enums.async_task_status import AsyncTaskStatus
                session.add_all([AsyncTaskModel(owner_id=client.owner, type="ppt_workflow", status=AsyncTaskStatus.PENDING,
                                                payload={"stage": "awaiting_content"}) for _ in range(105)])
                await session.commit()
                assert await fail_interrupted_async_tasks(session) == 0
            await sweep(sessions)
            status = await client.call("task", inputs.Task(taskRef=result["taskRef"]))
            assert status["status"] == "completed" and len(status["committed"]) == 2
    asyncio.run(run())


def test_export_failure_reports_phase_and_recovery_clears_old_errors(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from services.agent_tools.workflow_worker import record_failure
    import services.agent_tools.workflow_worker as worker
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            result, queued, task_id = await queue_export(client)
            for _ in range(3):
                await record_failure(sessions, client.owner, task_id, HTTPException(500, {
                    "code": "snapshot_render_timeout", "phase": "preview_dom", "private": "never publish"}))
            failed = await client.call("task", inputs.Task(taskRef=queued["taskRef"]))
            assert failed["status"] == "technical_error"
            assert failed["errors"] == [{"code": "snapshot_render_timeout", "phase": "preview_dom", "type": "HTTPException"}]
            recovered = await client.call("task", inputs.Task(taskRef=queued["taskRef"], action="resume"))
            assert recovered["status"] == "export_queued"
            async def render(owner, task, claim, snapshot, formats):
                assert snapshot["documentId"] == result["documentId"]
                assert len(snapshot["slides"]) == 2
                return {"exports": {format: {"status": "completed", "url": "test"} for format in formats}, "previews": []}
            monkeypatch.setattr(worker, "render_snapshot", render)
            await export_one(sessions, client.owner, task_id)
            done = await client.call("task", inputs.Task(taskRef=queued["taskRef"]))
            assert done["status"] == "completed" and done["errors"] == [] and done["retryAt"] == 0
    asyncio.run(run())


def test_renderer_diagnostic_survives_python_boundary_without_page_content(tmp_path, monkeypatch, caplog):
    from fastapi import HTTPException
    from services.agent_tools.workflow_export import render_snapshot
    from services.export_task_service import ExportTaskService
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    async def failed(self, task, detail):
        return {"error": {"code": "snapshot_render_timeout", "phase": "preview_dom", "raw": "private content"},
                "phases": [{"phase": "preview_dom", "status": "error", "durationMs": 30000}]}
    monkeypatch.setattr(ExportTaskService, "_run_task", failed)
    with pytest.raises(HTTPException) as error:
        asyncio.run(render_snapshot(uuid.uuid4(), "test-task", "claim", {"revision": 8, "slides": []}, ["pptx"]))
    assert error.value.detail == {"code": "snapshot_render_timeout", "phase": "preview_dom"}
    assert "private content" not in caplog.text


def test_missing_snapshot_stops_without_regenerating_or_rewinding_the_document(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from services.agent_tools.workflow_export import snapshot_directory
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            result, queued, task_id = await queue_export(client)
            for path in snapshot_directory(client.owner, task_id).glob('*.json'):
                path.unlink()
            await export_one(sessions, client.owner, task_id)
            failed = await client.call("task", inputs.Task(taskRef=queued["taskRef"]))
            assert failed["status"] == "export_input_error"
            assert failed["errors"][0]["code"] == "export_snapshot_missing"
            with pytest.raises(HTTPException, match="task_requires_new_business_decision"):
                await client.call("task", inputs.Task(taskRef=queued["taskRef"], action="resume"))
            read = await client.call("read_for_edit", inputs.Read(documentId=result["documentId"]))
            assert read["revision"] == failed["revision"] and len(read["slides"]) == 2
    asyncio.run(run())


@pytest.mark.skipif(os.getenv("PRESENTON_TEST_RENDER") != "1", reason="explicit native renderer smoke is opt-in")
def test_real_fixed_snapshot_pptx_pdf_and_previews(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "assets"))
    monkeypatch.setenv("TEMP_DIRECTORY", str(tmp_path / "render-temp"))
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, queued, task_id = await queue_export(client)
            await export_one(sessions, client.owner, task_id)
            status = await client.call("task", inputs.Task(taskRef=queued["taskRef"]))
            assert status["status"] == "completed", status
            assert len(status["previews"]) == 2
            files = list((tmp_path / "assets").rglob("presentation.pptx"))
            assert len(files) == 1
            with zipfile.ZipFile(files[0]) as pptx:
                slides = [name for name in pptx.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
                assert len(slides) == 2
                notes = " ".join(pptx.read(name).decode() for name in pptx.namelist() if name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml"))
                assert "Note 0" in notes and "Note 1" in notes
            assert files[0].with_suffix(".pdf").read_bytes().startswith(b"%PDF-")
    asyncio.run(run())


@pytest.mark.skipif(os.getenv("PRESENTON_TEST_RENDER") != "1", reason="explicit native renderer regression is opt-in")
def test_real_six_slides_with_large_embedded_backgrounds(tmp_path, monkeypatch):
    """Data-URI backgrounds reproduce the missing network-idle event on Chromium."""
    import base64
    import random
    import struct
    import zlib
    from services.agent_tools.workflow_export import render_snapshot
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "assets"))
    monkeypatch.setenv("TEMP_DIRECTORY", str(tmp_path / "render-temp"))
    width, height = 1920, 1080
    pixels = random.Random(0).randbytes(width * height * 3)
    scanlines = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")
    data = "data:image/png;base64," + base64.b64encode(png).decode()
    ui = {"id": "embedded-background", "description": "Frozen background", "components": [{
        "id": "background", "description": "Full-slide background", "position": {"x": 0, "y": 0}, "elements": [{
        "type": "image", "name": "texture", "decorative": True, "is_icon": False, "fit": "fill",
        "position": {"x": 0, "y": 0}, "size": {"width": 1280, "height": 720}, "data": data,
    }]}]}
    snapshot = {"revision": 8, "title": "Embedded background regression", "slides": [
        {"index": i, "ui": ui, "speakerNote": f"Note {i}"} for i in range(6)]}
    result = asyncio.run(render_snapshot(uuid.uuid4(), "large-background", "attempt", snapshot, ["pptx", "pdf"]))
    assert all(item["status"] == "completed" for item in result["exports"].values())
    assert len(result["previews"]) == 6
    geometry = json.loads(next((tmp_path / "assets").rglob("geometry.json")).read_text())
    assert all(item == {"overflows": 0, "brokenImages": 0} for item in geometry)
