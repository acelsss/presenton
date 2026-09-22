"""Real native HTTP saves interoperate with MCP page edits, without internal AI."""
import asyncio
import copy
import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from models.sql.agent_document import AgentPageRevision
from services.agent_tools import workflow_schemas as inputs
from services.agent_tools.editor import EditorService
from tests.unit.test_agent_tools import database
from tests.unit.test_agent_tools_api import app_for_sessions
from tests.unit.test_ppt_workflow import WorkflowClient, start, complete
from tests.unit.test_ppt_workflow_subagents import change


async def setup(sessions, owners, monkeypatch, count=2):
    agent = WorkflowClient(sessions, owners[0].owner)
    _, created = await start(agent, count)
    await complete(agent, created)
    app = app_for_sessions(sessions, agent.owner, monkeypatch)
    return agent, created["documentId"], app


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": "Bearer fixture-auth"})


def edit_page(slide, title):
    result = copy.deepcopy(slide)
    element = result["ui"]["components"][0]["elements"][0]
    element["text"] = title
    if "runs" in element:
        element["runs"] = [{"text": title}]
    result["speaker_note"] = title + " notes"
    return result


@pytest.mark.parametrize("external", ["true", "false"])
def test_native_visibility_owner_isolation_and_ordinary_saves(tmp_path, monkeypatch, external):
    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", external)
    monkeypatch.setenv("DISABLE_AUTH", "false")
    async def run():
        async with database(tmp_path) as (sessions, owners):
            agent, document, app = await setup(sessions, owners, monkeypatch)
            import api.middlewares as middleware
            monkeypatch.setattr(middleware, "maybe_proxy_presenton_cloud_request", AsyncMock(return_value=None))
            monkeypatch.setattr(middleware, "update_env_with_user_config", lambda: None)
            async with client(app) as api:
                listing = await api.get("/api/v1/ppt/presentation/all")
                assert listing.status_code == 200, listing.text
                assert document in [item["id"] for item in listing.json()]
                read = await api.get(f"/api/v1/ppt/presentation/{document}")
                assert read.status_code == 200, read.text
                assert read.json()["coordination"]["writable"]
                assert len(read.json()["coordination"]["pageRevisions"]) == 2
                manual = await api.patch("/api/v1/ppt/presentation/slide_update", json={
                    "slide": edit_page(read.json()["slides"][0], "Works in both modes"), "expectedPageRevision": 1})
                assert manual.status_code == 200, manual.text
                duplicate = await api.post(f"/api/v1/ppt/presentation/{document}/duplicate")
                assert duplicate.status_code == 200, duplicate.text
                slide = edit_page(duplicate.json()["slides"][0], "Ordinary edit")
                saved = await api.patch("/api/v1/ppt/presentation/slide_update", json={"slide": slide})
                assert saved.status_code == 200, saved.text
                assert "X-Presenton-Coordination" not in saved.headers
            other_app = app_for_sessions(sessions, owners[1].owner, monkeypatch)
            monkeypatch.setattr(middleware, "maybe_proxy_presenton_cloud_request", AsyncMock(return_value=None))
            monkeypatch.setattr(middleware, "update_env_with_user_config", lambda: None)
            async with client(other_app) as api:
                assert (await api.get(f"/api/v1/ppt/presentation/{document}")).status_code == 404
                denied = await api.patch("/api/v1/ppt/presentation/slide_update", json={
                    "slide": read.json()["slides"][0], "expectedPageRevision": 1})
                assert denied.status_code == 404
    asyncio.run(run())


def test_native_and_mcp_edit_different_pages_then_reject_same_page(tmp_path, monkeypatch):
    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    monkeypatch.setenv("DISABLE_AUTH", "false")
    async def run():
        async with database(tmp_path) as (sessions, owners):
            agent, document, app = await setup(sessions, owners, monkeypatch)
            async with client(app) as api:
                before = (await api.get(f"/api/v1/ppt/presentation/{document}")).json()
                snapshot = await agent.call("read_for_edit", inputs.Read(documentId=document))
                request = {"slide": edit_page(before["slides"][0], "Manual"), "expectedPageRevision": 1}
                saved, mcp = await asyncio.gather(
                    api.patch("/api/v1/ppt/presentation/slide_update", json=request),
                    agent.call("edit", change(document, snapshot["slides"][1], "Subagent")))
                assert saved.status_code == 200, saved.text
                assert mcp["status"] == "applied"
                coordination = json.loads(saved.headers["X-Presenton-Coordination"])
                assert coordination["pageRevisions"] == {request["slide"]["id"]: 2}
                replay = await api.patch("/api/v1/ppt/presentation/slide_update", json=request)
                assert replay.status_code == 200, replay.text
                assert json.loads(replay.headers["X-Presenton-Coordination"]) == coordination
                stale = await api.patch("/api/v1/ppt/presentation/slide_update", json={
                    **request, "slide": edit_page(request["slide"], "Stale")})
                assert stale.status_code == 409
                assert stale.json()["detail"]["code"] == "page_revision_conflict"
                missing = await api.patch("/api/v1/ppt/presentation/slide_update", json={"slide": request["slide"]})
                assert missing.status_code == 428
                after = await agent.call("read_for_edit", inputs.Read(documentId=document))
                assert [row["pageRevision"] for row in after["slides"]] == [2, 2]
                assert after["slides"][0]["speakerNote"] == "Manual notes"
                assert (await agent.call("edit", change(document, after["slides"][0], "Agent continues")))["status"] == "applied"
                superseded = await api.patch("/api/v1/ppt/presentation/slide_update", json=request)
                assert superseded.status_code == 409
    asyncio.run(run())


def test_native_structure_keeps_ids_versions_and_rejects_stale_deck(tmp_path, monkeypatch):
    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    monkeypatch.setenv("DISABLE_AUTH", "false")
    async def run():
        async with database(tmp_path) as (sessions, owners):
            agent, document, app = await setup(sessions, owners, monkeypatch, 3)
            async with client(app) as api:
                before = (await api.get(f"/api/v1/ppt/presentation/{document}")).json()
                reordered = list(reversed(before["slides"]))
                for index, row in enumerate(reordered): row["index"] = index
                payload = {"id": document, "slides": reordered, "n_slides": 3,
                           "expectedRevision": before["coordination"]["revision"],
                           "expectedPageRevisions": before["coordination"]["pageRevisions"]}
                saved = await api.patch("/api/v1/ppt/presentation/update", json=payload)
                assert saved.status_code == 200, saved.text
                assert saved.json()["coordination"]["pageRevisions"] == before["coordination"]["pageRevisions"]
                assert [s["id"] for s in saved.json()["slides"]] == [s["id"] for s in reordered]
                latest = saved.json()
                added = copy.deepcopy(reordered[0]); added["id"] = str(uuid.uuid4())
                rows = [reordered[0], added]
                for index, row in enumerate(rows): row["index"] = index
                changed = await api.patch("/api/v1/ppt/presentation/update", json={
                    "id": document, "slides": rows, "n_slides": 2,
                    "expectedRevision": latest["coordination"]["revision"],
                    "expectedPageRevisions": latest["coordination"]["pageRevisions"]})
                assert changed.status_code == 200, changed.text
                assert changed.json()["coordination"]["pageRevisions"] == {row["id"]: 1 for row in rows}
                stale = await api.patch("/api/v1/ppt/presentation/update", json={**payload, "title": "Stale"})
                assert stale.status_code == 409
                read = await agent.call("read_for_edit", inputs.Read(documentId=document))
                assert len(read["slides"]) == len(read["outlines"]["slides"]) == 2
                async with sessions() as session:
                    versions = list(await session.scalars(select(AgentPageRevision)))
                    assert {str(row.slide_id) for row in versions} == {row["id"] for row in rows}
                restored = copy.deepcopy(before["slides"][1])
                restored["index"] = 2
                undo = await api.patch("/api/v1/ppt/presentation/update", json={
                    "id": document, "slides": [*rows, restored], "n_slides": 3,
                    "expectedRevision": changed.json()["coordination"]["revision"],
                    "expectedPageRevisions": changed.json()["coordination"]["pageRevisions"]})
                assert undo.status_code == 200, undo.text
                assert undo.json()["coordination"]["pageRevisions"][restored["id"]] == 2
                stale_incarnation = await api.patch("/api/v1/ppt/presentation/slide_update", json={
                    "slide": edit_page(restored, "Before deletion"), "expectedPageRevision": 1})
                assert stale_incarnation.status_code == 409
    asyncio.run(run())


def test_native_receipt_failure_rolls_back_ui_and_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    monkeypatch.setenv("DISABLE_AUTH", "false")
    async def run():
        async with database(tmp_path) as (sessions, owners):
            agent, document, app = await setup(sessions, owners, monkeypatch)
            async with client(app) as api:
                before = (await api.get(f"/api/v1/ppt/presentation/{document}")).json()
                async def fail_receipt(*args, **kwargs): raise RuntimeError("receipt failure")
                monkeypatch.setattr(EditorService, "_record", fail_receipt)
                with pytest.raises(RuntimeError, match="receipt failure"):
                    await api.patch("/api/v1/ppt/presentation/slide_update", json={
                        "slide": edit_page(before["slides"][0], "Must roll back"), "expectedPageRevision": 1})
                after = (await api.get(f"/api/v1/ppt/presentation/{document}")).json()
                assert after == before
    asyncio.run(run())


def test_native_metadata_preserves_theme_and_unseen_page_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    monkeypatch.setenv("DISABLE_AUTH", "false")
    async def run():
        async with database(tmp_path) as (sessions, owners):
            agent, document, app = await setup(sessions, owners, monkeypatch)
            async with client(app) as api:
                before = (await api.get(f"/api/v1/ppt/presentation/{document}")).json()
                saved = await api.patch("/api/v1/ppt/presentation/update", json={
                    "id": document, "title": "Renamed", "expectedRevision": before["coordination"]["revision"]})
                assert saved.status_code == 200, saved.text
                assert saved.json()["theme"] == before["theme"]
                assert saved.json()["coordination"]["pageRevisions"] == before["coordination"]["pageRevisions"]
                read = await agent.call("read_for_edit", inputs.Read(documentId=document))
                await agent.call("edit", change(document, read["slides"][1], "External change"))
                # Even with a fresh document revision, an old whole-deck payload
                # must not overwrite pages that the browser has never read.
                newest = await agent.call("read_for_edit", inputs.Read(documentId=document))
                stale_pages = await api.patch("/api/v1/ppt/presentation/update", json={
                    "id": document, "slides": before["slides"], "expectedRevision": newest["revision"],
                    "expectedPageRevisions": before["coordination"]["pageRevisions"]})
                assert stale_pages.status_code == 409
    asyncio.run(run())


def test_model_routes_blocked_after_auth_but_native_queries_available(tmp_path, monkeypatch):
    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    monkeypatch.setenv("DISABLE_AUTH", "false")
    async def run():
        async with database(tmp_path) as (sessions, owners):
            _, document, app = await setup(sessions, owners, monkeypatch)
            async with client(app) as api:
                paths = [("POST", "/presentation/generate/async"), ("GET", f"/presentation/stream/{document}"),
                         ("GET", f"/outlines/stream/{document}"), ("POST", "/chat/message"),
                         ("POST", "/slide/edit"), ("GET", "/images/generate"),
                         ("POST", "/template/layouts/generate"), ("POST", "/theme/generate")]
                for method, path in paths:
                    denied = await api.request(method, "/api/v1/ppt" + path, json={})
                    assert denied.status_code == 409, (path, denied.text)
                    assert denied.json()["detail"]["code"] == "internal_generation_disabled"
                for path in ("/api/v1/ppt/template/all", "/api/v1/ppt/themes/default"):
                    allowed = await api.get(path)
                    assert allowed.status_code == 200, (path, allowed.text)
                # Source import initialization only converts an uploaded file;
                # allow normal payload validation without executing a model.
                imported = await api.post("/api/v1/ppt/template/init", json={})
                assert imported.status_code == 422, imported.text
                assert "internal_generation_disabled" not in imported.text
                api.headers.clear()
                assert (await api.post("/api/v1/ppt/presentation/generate/async", json={})).status_code == 401
    asyncio.run(run())
