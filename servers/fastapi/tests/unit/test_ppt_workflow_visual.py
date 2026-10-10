import asyncio
import copy
import uuid
import pytest
from fastapi import HTTPException
from services.agent_tools import workflow_schemas as inputs
from services.agent_tools.workflow import WorkflowService
from services.agent_tools import workflow_visual as visual
from services.agent_tools.workflow_worker import drive
from models.sql.ppt_workflow import PptWorkflowRef
from tests.unit.test_agent_tools import database
from tests.unit.test_ppt_workflow import WorkflowClient, start, pages


def test_latest_png_delivery_review_repair_and_completion_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    class Renderer:
        async def request(self, payload):
            from pathlib import Path
            Path(payload["outputDirectory"], "preview-1.png").write_bytes(b"\x89PNG\r\n\x1a\nfixed-test-render")
            return {}
    async def renderer(*args):
        return Renderer()
    monkeypatch.setattr(visual.generation_renderers, "get", renderer)

    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            prepared = await client.call("prepare", inputs.Prepare(title="PNG review", confirmed=True, outline=["# Page"]))
            result = await client.call("start", inputs.Start(preparationRef=prepared["preparationRef"],
                templateRef=prepared["templates"][0]["templateRef"], layoutPlan=["intro"], visualReviewRequired=True))
            await client.call("submit", inputs.Submit(taskRef=result["taskRef"], pages=pages(result), finish=True))
            async with sessions() as session:
                task_id = (await session.get(PptWorkflowRef, result["taskRef"])).task_id
            await drive(sessions, client.owner, task_id, budget_seconds=10)
            status = await client.call("task", inputs.Task(taskRef=result["taskRef"]))
            assert status["status"] == "awaiting_finish" and status["errors"][0]["code"] == "visual_review_required"
            slot = status["slots"][0]
            context = "run-a:execution-a"
            async def call(fn, request, owner=None):
                async with sessions() as session:
                    return await fn(WorkflowService(session, owner or client.owner, "visual-test"), request)
            read = inputs.VisualRead(taskRef=result["taskRef"], pages=[{"slideId": slot["slideId"],
                "expectedPageRevision": slot["pageRevision"]}], deliveryContext=context)
            evidence = (await call(visual.visual_read, read))["images"][0]
            report = inputs.VisualReview(taskRef=result["taskRef"], evidenceId=evidence["evidenceId"],
                verdict="needs_changes", issues=["Title is obscured; move its element"], deliveryContext=context)
            with pytest.raises(HTTPException, match="visual_image_not_delivered"):
                await call(visual.visual_review, report)
            with pytest.raises(HTTPException, match="workflow_reference_not_found"):
                await call(visual.visual_read, read, owners[1].owner)
            ack = inputs.VisualAck(taskRef=result["taskRef"], evidenceIds=[evidence["evidenceId"]], deliveryContext=context)
            wrong_context = ack.model_copy(update={"delivery_context": "other-run:execution"})
            with pytest.raises(HTTPException, match="visual_evidence_not_found"):
                await call(visual.visual_ack, wrong_context)
            await call(visual.visual_ack, ack)
            first_review = await call(visual.visual_review, report)
            assert (await call(visual.visual_review, report)) == first_review
            different = report.model_copy(update={"verdict": "pass", "issues": []})
            with pytest.raises(HTTPException, match="visual_verdict_already_recorded"):
                await call(visual.visual_review, different)
            edit = inputs.Edit(documentId=result["documentId"], taskRef=result["taskRef"], pageEdits=[{
                "slideId": slot["slideId"], "expectedPageRevision": slot["pageRevision"], "operations": [{
                    "tool": "updateElement", "arguments": {"elementPath": "components[0].elements[0]", "text": "Readable title"}}]}])
            changed = await client.call("edit", edit)
            edit.page_edits[0].expected_page_revision = changed["pages"][0]["pageRevision"]
            with pytest.raises(HTTPException, match="visual_repair_not_authorized"):
                await client.call("edit", edit)
            report.verdict, report.issues = "pass", []
            with pytest.raises(HTTPException, match="visual_evidence_stale"):
                await call(visual.visual_review, report)
            read.pages[0].expected_page_revision = changed["pages"][0]["pageRevision"]
            new = (await call(visual.visual_read, read))["images"][0]
            assert new["evidenceId"] != evidence["evidenceId"]
            ack.evidence_ids = [new["evidenceId"]]
            report.evidence_id = new["evidenceId"]
            await call(visual.visual_ack, ack)
            inconsistent = report.model_copy(update={"issues": ["Unresolved title"]})
            with pytest.raises(HTTPException, match="visual_pass_has_unresolved_issues"):
                await call(visual.visual_review, inconsistent)
            await call(visual.visual_review, report)
            # A changed global theme invalidates an otherwise current page pass.
            from models.sql.presentation import PresentationModel
            async with sessions() as session:
                session.info["agent_tools"] = True
                presentation = await session.get(PresentationModel, uuid.UUID(result["documentId"]))
                original_theme = copy.deepcopy(presentation.theme)
                presentation.theme = {"background": "#101010"}
                await session.commit()
            with pytest.raises(HTTPException, match="visual_evidence_stale"):
                await call(visual.visual_review, report)
            await client.call("submit", inputs.Submit(taskRef=result["taskRef"], pages=[], finish=True))
            await drive(sessions, client.owner, task_id, budget_seconds=10)
            assert (await client.call("task", inputs.Task(taskRef=result["taskRef"])))["status"] == "awaiting_finish"
            async with sessions() as session:
                session.info["agent_tools"] = True
                presentation = await session.get(PresentationModel, uuid.UUID(result["documentId"]))
                presentation.theme = original_theme
                await session.commit()
            # A late repeated read cannot erase delivery or an already-recorded verdict.
            assert (await call(visual.visual_read, read))["images"][0]["evidenceId"] == new["evidenceId"]
            await call(visual.visual_review, report)
            await client.call("submit", inputs.Submit(taskRef=result["taskRef"], pages=[], finish=True))
            await drive(sessions, client.owner, task_id, budget_seconds=10)
            final = await client.call("task", inputs.Task(taskRef=result["taskRef"]))
            assert final["status"] == "completed"
            assert final["visualReviews"][slot["slideId"]]["attempt"] == 2
    asyncio.run(run())


def test_cross_task_evidence_and_three_review_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    class Renderer:
        async def request(self, payload):
            from pathlib import Path
            Path(payload["outputDirectory"], "preview-1.png").write_bytes(b"\x89PNG\r\n\x1a\nlimit-fixture")
            return {}
    async def renderer(*args):
        return Renderer()
    monkeypatch.setattr(visual.generation_renderers, "get", renderer)
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            prepared = await client.call("prepare", inputs.Prepare(title="Review limit", confirmed=True, outline=["Page"]))
            result = await client.call("start", inputs.Start(preparationRef=prepared["preparationRef"],
                templateRef=prepared["templates"][0]["templateRef"], layoutPlan=["intro"], visualReviewRequired=True))
            await client.call("submit", inputs.Submit(taskRef=result["taskRef"], pages=pages(result)))
            async with sessions() as session:
                task_id = (await session.get(PptWorkflowRef, result["taskRef"])).task_id
            await drive(sessions, client.owner, task_id, budget_seconds=10)
            slot = (await client.call("task", inputs.Task(taskRef=result["taskRef"])))["slots"][0]
            async def call(fn, request):
                async with sessions() as session:
                    return await fn(WorkflowService(session, client.owner, "limit-test"), request)
            other_prepared = await client.call("prepare", inputs.Prepare(title="Other task", confirmed=True, outline=["Page"]))
            other = await client.call("start", inputs.Start(preparationRef=other_prepared["preparationRef"],
                templateRef=other_prepared["templates"][0]["templateRef"], layoutPlan=["intro"], visualReviewRequired=True))
            for attempt in range(3):
                context = f"run-{attempt}:execution"
                read = inputs.VisualRead(taskRef=result["taskRef"], pages=[{"slideId": slot["slideId"],
                    "expectedPageRevision": slot["pageRevision"]}], deliveryContext=context)
                evidence = (await call(visual.visual_read, read))["images"][0]
                ack = inputs.VisualAck(taskRef=result["taskRef"], evidenceIds=[evidence["evidenceId"]], deliveryContext=context)
                with pytest.raises(HTTPException, match="visual_evidence_not_found"):
                    await call(visual.visual_ack, ack.model_copy(update={"task_ref": other["taskRef"]}))
                await call(visual.visual_ack, ack)
                report = await call(visual.visual_review, inputs.VisualReview(taskRef=result["taskRef"],
                    evidenceId=evidence["evidenceId"], deliveryContext=context, verdict="needs_changes", issues=["Contrast defect"]))
                assert report["review"]["attempt"] == attempt + 1
            with pytest.raises(HTTPException, match="visual_review_limit_reached"):
                await call(visual.visual_read, read.model_copy(update={"delivery_context": "run-4:execution"}))
            await client.call("submit", inputs.Submit(taskRef=result["taskRef"], pages=[], finish=True))
            await drive(sessions, client.owner, task_id, budget_seconds=10)
            assert (await client.call("task", inputs.Task(taskRef=result["taskRef"])))["status"] == "awaiting_finish"
    asyncio.run(run())
