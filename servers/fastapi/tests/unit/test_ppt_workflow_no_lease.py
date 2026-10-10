"""Lease-free edits, reconnects, atomic retries and completed-document guidance."""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from models.sql.agent_document import AgentCallerSession, AgentDocument, AgentOperationReceipt
from models.sql.async_task import AsyncTaskModel
from models.sql.ppt_workflow import PptWorkflowRef
from services.agent_tools import workflow_schemas as inputs
from services.agent_tools.workflow import WorkflowService
from services.agent_tools.workflow_worker import drive
from tests.unit.test_agent_tools import database
from tests.unit.test_ppt_workflow import WorkflowClient, start, complete, pages


def edit_request(document, revision, text):
    return inputs.Edit(documentId=document, expectedRevision=revision, operations=[{
        "tool": "updateElement", "arguments": {
            "index": 0, "elementPath": "components[0].elements[0]", "text": text}}])


def test_expired_legacy_metadata_never_blocks_reconnection_or_draft_submission(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            first = WorkflowClient(sessions, owners[0].owner)
            _, created = await start(first, 1)
            async with sessions() as session:
                task_ref = await session.get(PptWorkflowRef, created["taskRef"])
                task_id = task_ref.task_id
                task = await session.get(AsyncTaskModel, task_id)
                task.payload = {**task.payload, "deadline": 1, "epoch": 99, "callerId": str(uuid.uuid4())}
                state = await session.get(AgentDocument, uuid.UUID(created["documentId"]))
                state.writer_epoch, state.writer_expires_at = 99, 1
                for ref in await session.scalars(select(PptWorkflowRef)):
                    ref.expires_at = 1
                await session.commit()
            second = WorkflowClient(sessions, first.owner, "reconnected-client")
            resumed = await second.call("task", inputs.Task(taskRef=created["taskRef"], action="resume"))
            assert resumed["status"] == "awaiting_content"
            saved = await second.call("submit", inputs.Submit(taskRef=created["taskRef"], pages=pages(created)))
            assert saved["status"] == "queued"
            await drive(sessions, first.owner, task_id, budget_seconds=10)
            read = await second.call("read_for_edit", inputs.Read(documentId=created["documentId"]))
            assert read["phase"] == "ready" and "writer" not in read
            assert (await second.call("edit", edit_request(read["documentId"], read["revision"], "After reconnect")))["status"] == "applied"
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(AgentCallerSession)) == 0
    asyncio.run(run())


def test_start_replay_reports_current_state_and_completed_submit_points_to_edit(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            request, created = await start(client, 1)
            done = await complete(client, created)
            replay = await client.call("start", request)
            assert replay["replayed"] and replay["status"] == "completed"
            assert replay["receiptStatus"] == "awaiting_content"
            assert replay["revision"] == done["revision"] and replay["currentPhase"] == "ready"
            assert replay["nextTool"] == "ppt_read"
            changed = pages(created)
            changed[0].speaker_note = "New edit intent"
            for target in ({"taskRef": created["taskRef"]}, {"editRef": created["editRef"]}):
                with pytest.raises(HTTPException) as error:
                    await client.call("submit", inputs.Submit(**target, pages=changed))
                assert error.value.detail["code"] == "document_completed_use_ppt_edit"
                assert error.value.detail["nextTool"] == "ppt_read"
                assert error.value.detail["documentId"] == created["documentId"]
    asyncio.run(run())


def test_new_edits_remain_possible_and_only_stale_versions_conflict(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, created = await start(client, 1)
            done = await complete(client, created)
            request = edit_request(created["documentId"], done["revision"], "A")
            first = await client.call("edit", request)
            second = WorkflowClient(sessions, client.owner, "another-client")
            next_edit = await second.call("edit", edit_request(created["documentId"], first["revision"], "B"))
            back = await client.call("edit", edit_request(created["documentId"], next_edit["revision"], "A"))
            assert back["revision"] == done["revision"] + 3
            retry = await second.call("edit", request)
            assert retry["replayed"] and retry["receiptRevision"] == first["revision"]
            assert retry["currentRevision"] == back["revision"]
            with pytest.raises(HTTPException, match="needs_rebase"):
                await second.call("edit", edit_request(created["documentId"], first["revision"], "Stale"))
            with pytest.raises(HTTPException) as error:
                await WorkflowClient(sessions, owners[1].owner).call("edit", request)
            assert error.value.status_code == 404
    asyncio.run(run())


def test_concurrent_identical_edits_replay_but_different_stale_edits_do_not_overwrite(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, created = await start(client, 1)
            done = await complete(client, created)
            request = edit_request(created["documentId"], done["revision"], "Same request")
            results = await asyncio.wait_for(asyncio.gather(*[client.call("edit", request) for _ in range(4)]), 10)
            assert sum(not item.get("replayed", False) for item in results) == 1
            revision = results[0]["revision"]
            results = await asyncio.wait_for(asyncio.gather(
                client.call("edit", edit_request(created["documentId"], revision, "Client A")),
                client.call("edit", edit_request(created["documentId"], revision, "Client B")),
                return_exceptions=True), 10)
            assert sum(isinstance(item, HTTPException) and item.detail["code"] == "needs_rebase" for item in results) == 1
            read = await client.call("read_for_edit", inputs.Read(documentId=created["documentId"]))
            assert read["revision"] == revision + 1
    asyncio.run(run())


def test_edit_receipt_failure_rolls_back_ui_revision_and_native_receipt(tmp_path, monkeypatch):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, created = await start(client, 1)
            await complete(client, created)
            before = await client.call("read_for_edit", inputs.Read(documentId=created["documentId"]))
            original = WorkflowService.accept
            async def interrupted(self, ref, request, result):
                raise RuntimeError("response receipt failed")
            monkeypatch.setattr(WorkflowService, "accept", interrupted)
            request = edit_request(created["documentId"], before["revision"], "Must roll back")
            with pytest.raises(RuntimeError):
                await client.call("edit", request)
            after = await client.call("read_for_edit", inputs.Read(documentId=created["documentId"]))
            assert after["revision"] == before["revision"] and after["slides"] == before["slides"]
            async with sessions() as session:
                assert await session.scalar(select(func.count()).select_from(AgentOperationReceipt)) == 2
            monkeypatch.setattr(WorkflowService, "accept", original)
            assert (await client.call("edit", request))["revision"] == before["revision"] + 1
    asyncio.run(run())


def test_export_is_deduplicated_without_consuming_edit_permission(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, created = await start(client, 1)
            done = await complete(client, created)
            request = inputs.Export(documentId=created["documentId"], expectedRevision=done["revision"])
            exports = await asyncio.gather(client.call("export", request), client.call("export", request))
            assert exports[0]["taskRef"] == exports[1]["taskRef"]
            assert (await client.call("edit", edit_request(created["documentId"], done["revision"], "Edit after export")))["status"] == "applied"
            retry = await client.call("export", request)
            assert retry["replayed"] and retry["taskRef"] == exports[0]["taskRef"]
            assert retry["revision"] == done["revision"] and retry["currentRevision"] == done["revision"] + 1
    asyncio.run(run())
