"""Independent subagents share documents but coordinate versions per page."""
import asyncio
import copy
import uuid

import pytest
from fastapi import HTTPException

from models.sql.ppt_workflow import PptWorkflowRef
from services.agent_tools import workflow_schemas as inputs
from services.agent_tools.workflow_worker import drive
from tests.unit.test_agent_tools import database
from tests.unit.test_ppt_workflow import WorkflowClient, start, complete, pages


def change(document_id, slide, text):
    return inputs.Edit(documentId=document_id, pageEdits=[{
        "slideId": slide["slideId"], "expectedPageRevision": slide["pageRevision"],
        "operations": [{"tool": "updateElement", "arguments": {
            "elementPath": "components[0].elements[0]", "text": text}}]}])


def test_six_agents_submit_distinct_draft_slots_without_missing_page_retries(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            parent = WorkflowClient(sessions, owners[0].owner)
            _, created = await start(parent, 6)
            candidates = pages(created)
            async def child(i):
                agent = WorkflowClient(sessions, parent.owner, f"subagent-{i}")
                request = inputs.Submit(taskRef=created["taskRef"], pages=[candidates[i]], finish=False)
                response = await agent.call("submit", request)
                assert response["status"] in {"awaiting_content", "queued"}
                assert response["errors"] == []
                retry = await agent.call("submit", request)
                assert retry["replayed"]
            await asyncio.wait_for(asyncio.gather(*(child(i) for i in (5, 3, 1, 4, 2, 0))), 15)
            async with sessions() as session:
                task_id = (await session.get(PptWorkflowRef, created["taskRef"])).task_id
            await drive(sessions, parent.owner, task_id, budget_seconds=10)
            status = await parent.call("task", inputs.Task(taskRef=created["taskRef"]))
            assert status["status"] == "awaiting_finish" and len(status["committed"]) == 6
            await parent.call("submit", inputs.Submit(taskRef=created["taskRef"], pages=[], finish=True))
            await drive(sessions, parent.owner, task_id)
            read = await parent.call("read_for_edit", inputs.Read(documentId=created["documentId"]))
            assert len(read["slides"]) == 6 and all(s["pageRevision"] == 1 for s in read["slides"])
            assert [s["speakerNote"] for s in read["slides"]] == [f"Note {i}" for i in range(6)]
    asyncio.run(run())


def test_six_agents_edit_different_pages_from_one_snapshot_without_conflicts(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            parent = WorkflowClient(sessions, owners[0].owner)
            _, created = await start(parent, 6)
            await complete(parent, created)
            before = await parent.call("read_for_edit", inputs.Read(documentId=created["documentId"]))
            async def child(i):
                agent = WorkflowClient(sessions, parent.owner, f"editor-{i}")
                slide = before["slides"][i]
                read = await agent.call("read_for_edit", inputs.Read(documentId=before["documentId"], slideIds=[slide["slideId"]]))
                assert len(read["slides"]) == 1
                request = change(before["documentId"], slide, f"Agent {i} changed only this page")
                response = await agent.call("edit", request)
                assert response["status"] == "applied"
                assert response["pages"] == [{"slideId": slide["slideId"], "pageRevision": slide["pageRevision"] + 1}]
                assert (await agent.call("edit", request))["replayed"]
            await asyncio.wait_for(asyncio.gather(*(child(i) for i in range(6))), 15)
            after = await parent.call("read_for_edit", inputs.Read(documentId=before["documentId"]))
            assert after["revision"] == before["revision"] + 6
            for i, slide in enumerate(after["slides"]):
                assert slide["slideId"] == before["slides"][i]["slideId"]
                assert slide["pageRevision"] == before["slides"][i]["pageRevision"] + 1
                assert slide["speakerNote"] == before["slides"][i]["speakerNote"]
                assert slide["ui"]["components"][1] == before["slides"][i]["ui"]["components"][1]
    asyncio.run(run())


def test_same_page_conflicts_are_local_atomic_and_new_versions_allow_repeated_intents(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, created = await start(client, 2)
            await complete(client, created)
            before = await client.call("read_for_edit", inputs.Read(documentId=created["documentId"]))
            document = before["documentId"]
            requests = [change(document, before["slides"][0], text) for text in ("A", "B")]
            outcomes = await asyncio.gather(*(client.call("edit", r) for r in requests), return_exceptions=True)
            conflicts = [x for x in outcomes if isinstance(x, HTTPException)]
            assert len(conflicts) == 1 and conflicts[0].detail["code"] == "page_revision_conflict"
            batch = inputs.Edit(documentId=document, pageEdits=[
                *change(document, before["slides"][1], "Must roll back").page_edits,
                *change(document, before["slides"][0], "Stale").page_edits])
            with pytest.raises(HTTPException, match="page_revision_conflict"):
                await client.call("edit", batch)
            after = await client.call("read_for_edit", inputs.Read(documentId=document))
            assert after["slides"][1] == before["slides"][1]
            first = change(document, after["slides"][0], "Repeatable")
            applied = await client.call("edit", first)
            fresh = await client.call("read_for_edit", inputs.Read(documentId=document))
            await client.call("edit", change(document, fresh["slides"][0], "Other"))
            fresh = await client.call("read_for_edit", inputs.Read(documentId=document))
            again = await client.call("edit", change(document, fresh["slides"][0], "Repeatable"))
            assert again["pages"][0]["pageRevision"] == applied["pages"][0]["pageRevision"] + 2
            assert (await client.call("edit", first))["replayed"]
    asyncio.run(run())


def test_stable_slide_id_survives_reindexing_and_cannot_modify_document_structure(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, created = await start(client, 3)
            await complete(client, created)
            before = await client.call("read_for_edit", inputs.Read(documentId=created["documentId"]))
            assigned = before["slides"][2]
            await client.call("edit", inputs.Edit(documentId=before["documentId"], expectedRevision=before["revision"],
                                                 operations=[{"tool": "deleteSlide", "arguments": {"index": 0}}]))
            request = change(before["documentId"], assigned, "Stable identity")
            request.page_edits[0].operations[0].arguments["index"] = 2  # Historical index is stale.
            assert (await client.call("edit", request))["status"] == "applied"
            after = await client.call("read_for_edit", inputs.Read(documentId=before["documentId"], slideIds=[assigned["slideId"]]))
            assert after["slides"][0]["index"] == 1
            assert after["slides"][0]["pageRevision"] == assigned["pageRevision"] + 1
            invalid = inputs.Edit(documentId=before["documentId"], pageEdits=[{
                "slideId": assigned["slideId"], "expectedPageRevision": after["slides"][0]["pageRevision"],
                "operations": [{"tool": "deleteSlide"}]}])
            with pytest.raises(HTTPException, match="page_edit_cannot_change_document_structure"):
                await client.call("edit", invalid)
            other = WorkflowClient(sessions, owners[1].owner)
            with pytest.raises(HTTPException) as error:
                await other.call("edit", request)
            assert error.value.status_code == 404
    asyncio.run(run())
