import asyncio
import copy
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

from models.sql.agent_document import AgentCallerSession, AgentDocument, AgentOperationReceipt
from models.sql.async_task import AsyncTaskModel
from models.sql.presentation import PresentationModel
from models.sql.ppt_workflow import PptWorkflowRef
from services.agent_tools import workflow_schemas as inputs
from services.agent_tools.workflow import WorkflowService
from services.agent_tools.workflow_worker import drive
from tests.unit.test_agent_tools import database, slide_content


class WorkflowClient:
    def __init__(self, sessions, owner, actor="test-instance-11111111111"):
        self.sessions, self.owner, self.actor = sessions, owner, actor

    async def call(self, method, payload):
        async with self.sessions() as session:
            return await getattr(WorkflowService(session, self.owner, self.actor), method)(payload)


async def start(client, count=6):
    prepared = await client.call("prepare", inputs.Prepare(title="六页演示", confirmed=True,
                            outline=[f"# 第 {i + 1} 页" for i in range(count)]))
    request = inputs.Start(preparationRef=prepared["preparationRef"],
                           templateRef=prepared["templates"][0]["templateRef"], layoutPlan=["intro"] * count)
    result = await client.call("start", request)
    return request, result


def pages(result):
    return [inputs.Page(slotId=slot["slotId"], content=slide_content(f"Page {i}"), speakerNote=f"Note {i}")
            for i, slot in enumerate(result["slots"])]


async def complete(client, result):
    submitted = await client.call("submit", inputs.Submit(editRef=result["editRef"], pages=pages(result)))
    assert submitted["status"] == "queued"
    async with client.sessions() as session:
        ref = await session.get(PptWorkflowRef, result["taskRef"])
        task_id = ref.task_id
    await drive(client.sessions, client.owner, task_id, budget_seconds=10)
    return await client.call("task", inputs.Task(taskRef=result["taskRef"]))


def test_three_calls_and_replays_do_not_duplicate_documents_or_pages(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            request, result = await start(client)
            assert (await client.call("start", request))["replayed"] is True
            done = await complete(client, result)
            assert done["status"] == "completed", done
            assert len(done["committed"]) == 6
            snapshot = await client.call("read_for_edit", inputs.Read(documentId=result["documentId"]))
            assert len(snapshot["slides"]) == 6
            assert [slide["speakerNote"] for slide in snapshot["slides"]] == [f"Note {i}" for i in range(6)]
            repeated = await client.call("submit", inputs.Submit(editRef=result["editRef"], pages=pages(result)))
            assert repeated["replayed"] is True
            async with sessions() as session:
                session.info["agent_tools"] = True
                assert await session.scalar(select(func.count()).select_from(PresentationModel)) == 1
                assert await session.scalar(select(func.count()).select_from(AgentOperationReceipt)) == 7
            # A separately prepared identical title/outline is a separate intent.
            _, second = await start(client)
            assert second["documentId"] != result["documentId"]
    asyncio.run(run())


def test_status_is_read_only_while_writer_is_reserved_and_refreshes_progress(tmp_path):
    from sqlalchemy import event
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, result = await start(client, 1)
            async with sessions() as session:
                ref = await session.get(PptWorkflowRef, result["taskRef"])
                task_id = ref.task_id
                task = await session.get(AsyncTaskModel, task_id)
                task.payload = {**task.payload, "snapshot": {"asset": "x" * 2_000_000}}
                await session.commit()
                before_updated = task.updated_at
            async with sessions() as reader, sessions() as writer:
                reader_service = WorkflowService(reader, client.owner, "another-client")
                writer_service = WorkflowService(writer, client.owner, "worker")
                task = await writer_service.load_task(task_id)
                request = inputs.Task(taskRef=result["taskRef"])
                statements = []
                engine = reader.bind.sync_engine
                def capture(conn, cursor, statement, parameters, context, executemany):
                    statements.append(statement)
                event.listen(engine, "before_cursor_execute", capture)
                try:
                    # SQLite BEGIN IMMEDIATE still permits readers, but another
                    # write reservation would block until the timeout.
                    status = await asyncio.wait_for(reader_service.task(request), 1)
                finally:
                    event.remove(engine, "before_cursor_execute", capture)
                assert status["status"] == "awaiting_content"
                assert not reader.in_transaction() and not reader.identity_map
                assert not any(s.lstrip().upper().startswith(("UPDATE", "INSERT", "BEGIN IMMEDIATE")) for s in statements)
                assert all("SELECT async_tasks.payload" not in s for s in statements)
                assert task.updated_at.replace(tzinfo=None) == before_updated.replace(tzinfo=None)
                task.payload = {**task.payload, "stage": "exporting"}
                await writer.commit()
                # Same SQL session must observe a newly committed status.
                assert (await reader_service.task(request))["status"] == "exporting"
            with pytest.raises(HTTPException) as error:
                await WorkflowClient(sessions, owners[1].owner).call("task", request)
            assert error.value.status_code == 404
    asyncio.run(run())


def test_start_rolls_back_document_and_task_then_replays_once(tmp_path, monkeypatch):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            preparation = await client.call("prepare", inputs.Prepare(title="原子建稿", confirmed=True, outline=["# 第一页"]))
            request = inputs.Start(preparationRef=preparation["preparationRef"],
                                   templateRef=preparation["templates"][0]["templateRef"], layoutPlan=["intro"])
            original = WorkflowService.accept

            async def crash_before_commit(self, ref, payload, result):
                await self.session.flush()
                state = await self.session.get(AgentDocument, uuid.UUID(result["documentId"]))
                assert state.workflow_task_id is not None
                assert state.writer_session_id is None and state.writer_expires_at == 0
                raise RuntimeError("interrupted after task creation")

            monkeypatch.setattr(WorkflowService, "accept", crash_before_commit)
            with pytest.raises(RuntimeError, match="after task creation"):
                await client.call("start", request)
            async with sessions() as session:
                session.info["agent_tools"] = True
                for model in (PresentationModel, AgentDocument, AgentCallerSession, AsyncTaskModel):
                    assert await session.scalar(select(func.count()).select_from(model)) == 0
                ref = await session.get(PptWorkflowRef, request.preparation_ref)
                assert ref.result is None and ref.request_hash is None
                assert await session.scalar(select(func.count()).select_from(PptWorkflowRef)) == 1

            monkeypatch.setattr(WorkflowService, "accept", original)
            result = await client.call("start", request)
            assert (await client.call("start", request))["replayed"] is True
            async with sessions() as session:
                session.info["agent_tools"] = True
                for model in (PresentationModel, AgentDocument, AsyncTaskModel):
                    assert await session.scalar(select(func.count()).select_from(model)) == 1
                assert await session.scalar(select(func.count()).select_from(AgentCallerSession)) == 0
                state = await session.get(AgentDocument, uuid.UUID(result["documentId"]))
                assert state.writer_session_id is None and state.writer_expires_at == 0
                presentation = await session.get(PresentationModel, state.id)
                assert len(presentation.outlines["slides"]) == 1 and state.template_id == "test-template"
            assert "sessionToken" not in result and "writerEpoch" not in result
            assert (await complete(client, result))["status"] == "completed"
    asyncio.run(run())


def test_all_schema_errors_return_together_and_only_bad_pages_are_resent(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, result = await start(client)
            candidates = pages(result)
            candidates[1].content = {}
            candidates[4].content["body"]["Bullets"] = []
            submitted = await client.call("submit", inputs.Submit(editRef=result["editRef"], pages=candidates))
            assert submitted["status"] == "needs_input"
            assert {error["slotId"] for error in submitted["errors"]} == {candidates[1].slot_id, candidates[4].slot_id}
            fixed = await client.call("submit", inputs.Submit(editRef=submitted["repairRef"], pages=[pages(result)[1], pages(result)[4]]))
            assert fixed["status"] == "queued"
            async with sessions() as session:
                ref = await session.get(PptWorkflowRef, result["taskRef"])
                task_id = ref.task_id
            await drive(sessions, client.owner, task_id, budget_seconds=10)
            assert (await client.call("task", inputs.Task(taskRef=result["taskRef"]))) ["status"] == "completed"
    asyncio.run(run())


def test_partial_commit_resume_preserves_slide_ids_and_receipts(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, result = await start(client)
            await client.call("submit", inputs.Submit(editRef=result["editRef"], pages=pages(result)))
            async with sessions() as session:
                ref = await session.get(PptWorkflowRef, result["taskRef"])
                task_id = ref.task_id
            first = await client.call("step", task_id)
            assert len(first["committed"]) == 1
            # A new service and SQL session represents restart: only persisted state is used.
            await drive(sessions, client.owner, task_id, budget_seconds=10)
            final = await client.call("task", inputs.Task(taskRef=result["taskRef"]))
            assert len(final["committed"]) == 6 and final["revision"] == 8
            assert await client.call("step", task_id) == final
    asyncio.run(run())


def test_scope_reconnect_and_revision_conflicts(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            request, result = await start(client, 1)
            with pytest.raises(HTTPException) as error:
                await WorkflowClient(sessions, owners[1].owner).call("start", request)
            assert error.value.status_code == 404
            other = WorkflowClient(sessions, owners[0].owner, "second-instance")
            # Same owner may reconnect immediately, without a writer takeover.
            assert (await other.call("start", request))["replayed"]
            assert (await other.call("task", inputs.Task(taskRef=result["taskRef"], action="resume")))["status"] == "awaiting_content"
            async with sessions() as session:
                state = await session.get(AgentDocument, uuid.UUID(result["documentId"]))
                state.revision += 1
                await session.commit()
            with pytest.raises(HTTPException, match="needs_rebase"):
                await client.call("submit", inputs.Submit(editRef=result["editRef"], pages=pages(result)))
    asyncio.run(run())


def test_concurrent_start_submission_and_two_workers_have_one_effect(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            prepared = await client.call("prepare", inputs.Prepare(title="race", confirmed=True, outline=["# One"]))
            request = inputs.Start(preparationRef=prepared["preparationRef"], templateRef=prepared["templates"][0]["templateRef"], layoutPlan=["intro"])
            started = await asyncio.wait_for(asyncio.gather(*[client.call("start", request) for _ in range(4)]), 10)
            assert len({item["documentId"] for item in started}) == 1
            assert sum(not item.get("replayed", False) for item in started) == 1
            result = started[0]
            a = inputs.Submit(editRef=result["editRef"], pages=pages(result))
            b = a.model_copy(deep=True)
            b.pages[0].speaker_note = "different intent"
            submissions = await asyncio.gather(client.call("submit", a), client.call("submit", b), return_exceptions=True)
            assert sum(isinstance(item, HTTPException) for item in submissions) == 1
            async with sessions() as session:
                ref = await session.get(PptWorkflowRef, result["taskRef"])
                task_id = ref.task_id
            await asyncio.gather(*[drive(sessions, client.owner, task_id) for _ in range(2)])
            final = await client.call("task", inputs.Task(taskRef=result["taskRef"]))
            assert final["status"] == "completed" and len(final["committed"]) == 1
    asyncio.run(run())


def test_crash_between_native_save_and_cursor_commit_rolls_back_page(tmp_path, monkeypatch):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, result = await start(client, 2)
            await client.call("submit", inputs.Submit(editRef=result["editRef"], pages=pages(result)))
            async with sessions() as session:
                ref = await session.get(PptWorkflowRef, result["taskRef"])
                task_id = ref.task_id
            original = WorkflowService._apply_tool
            async def crash(self, state, presentation, operation):
                await original(self, state, presentation, operation)
                raise RuntimeError("simulated crash before cursor commit")
            monkeypatch.setattr(WorkflowService, "_apply_tool", crash)
            with pytest.raises(RuntimeError):
                await client.call("step", task_id)
            monkeypatch.setattr(WorkflowService, "_apply_tool", original)
            status = await client.call("task", inputs.Task(taskRef=result["taskRef"]))
            assert status["committed"] == [] and status["revision"] == 1
            await drive(sessions, client.owner, task_id)
            read = await client.call("read_for_edit", inputs.Read(documentId=result["documentId"]))
            assert len(read["slides"]) == 2 and read["revision"] == 4
    asyncio.run(run())


def test_native_edit_preserves_ui_notes_and_replays_without_new_revision(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, result = await start(client, 1)
            await complete(client, result)
            before = await client.call("read_for_edit", inputs.Read(documentId=result["documentId"]))
            with pytest.raises(HTTPException, match="native_edit_tool_not_available"):
                await client.call("edit", inputs.Edit(editRef=before["editRef"], operations=[{"tool": "generateAssets"}]))
            request = inputs.Edit(editRef=before["editRef"], operations=[{"tool": "updateElement", "arguments": {
                "index": 0, "elementPath": "components[0].elements[0]", "text": "修订标题"}}])
            edited = await client.call("edit", request)
            assert edited["status"] == "applied", edited
            replay = await client.call("edit", request)
            assert replay["replayed"] and replay["revision"] == edited["revision"]
            after = await client.call("read_for_edit", inputs.Read(documentId=result["documentId"]))
            assert after["revision"] == before["revision"] + 1
            assert after["slides"][0]["slideId"] == before["slides"][0]["slideId"]
            assert after["slides"][0]["speakerNote"] == "Note 0"
            assert after["slides"][0]["ui"]["components"][1] == before["slides"][0]["ui"]["components"][1]
    asyncio.run(run())


def test_legacy_mutation_cannot_bypass_generation_and_cancel_fences_worker(tmp_path):
    from services.agent_tools.schemas import Mutation
    from services.agent_tools.service import AgentDocumentService
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, result = await start(client, 1)
            legacy = owners[0]
            await legacy.call("open_session", "legacy")
            with pytest.raises(HTTPException, match="workflow_in_progress"):
                await legacy.call("mutate", uuid.UUID(result["documentId"]), Mutation(operationId=uuid.uuid4(),
                    expectedRevision=1, writerEpoch=1, tool="saveSlide", arguments={}))
            await client.call("submit", inputs.Submit(editRef=result["editRef"], pages=pages(result)))
            cancelled = await client.call("task", inputs.Task(taskRef=result["taskRef"], action="cancel"))
            async with sessions() as session:
                ref = await session.get(PptWorkflowRef, result["taskRef"])
                task_id = ref.task_id
            assert (await client.call("step", task_id))["status"] == "cancelled"
            assert cancelled["committed"] == []
    asyncio.run(run())


def test_template_selection_uses_pinned_snapshot_and_initialization_rolls_back(tmp_path, monkeypatch):
    from models.sql.template_v2 import TemplateV2
    from services.agent_tools.service import OperationRejected
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            prepared = await client.call("prepare", inputs.Prepare(title="snapshot", confirmed=True, outline=["# One"]))
            async with sessions() as session:
                template = await session.get(TemplateV2, "test-template")
                template.layouts = {"layouts": []}
                await session.commit()
            request = inputs.Start(preparationRef=prepared["preparationRef"], templateRef=prepared["templates"][0]["templateRef"], layoutPlan=["intro"])
            original = WorkflowService._apply_tool
            async def fail_after_outline(self, state, presentation, operation):
                await original(self, state, presentation, operation)
                raise OperationRejected("simulated_initialization_failure")
            monkeypatch.setattr(WorkflowService, "_apply_tool", fail_after_outline)
            with pytest.raises(OperationRejected):
                await client.call("start", request)
            async with sessions() as session:
                session.info["agent_tools"] = True
                assert await session.scalar(select(func.count()).select_from(PresentationModel)) == 0
            monkeypatch.setattr(WorkflowService, "_apply_tool", original)
            result = await client.call("start", request)
            assert result["layouts"][0]["layoutId"] == "intro"
    asyncio.run(run())
