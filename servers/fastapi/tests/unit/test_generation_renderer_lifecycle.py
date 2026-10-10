import asyncio
import pytest
from enums.async_task_status import AsyncTaskStatus
from models.sql.async_task import AsyncTaskModel
from models.sql.ppt_workflow import PptWorkflowRef
from services.agent_tools import generation_renderer, workflow_schemas as inputs
from services.agent_tools.workflow_worker import renderer_lifecycle
from tests.unit.test_agent_tools import database
from tests.unit.test_ppt_workflow import WorkflowClient


@pytest.mark.parametrize("stage", ["completed", "cancelled", "technical_error", "deleted", "deadline"])
def test_terminal_or_expired_generation_releases_renderer(tmp_path, monkeypatch, stage):
    class Manager:
        def __init__(self):
            self.sessions, self.closed = {}, []
        async def close(self, owner, task):
            key = (str(owner), str(task))
            self.sessions.pop(key)
            self.closed.append(key)
    manager = Manager()
    monkeypatch.setattr(generation_renderer, "generation_renderers", manager)

    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            prepared = await client.call("prepare", inputs.Prepare(title="Lifecycle", confirmed=True, outline=["# One"]))
            started = await client.call("start", inputs.Start(preparationRef=prepared["preparationRef"],
                templateRef=prepared["templates"][0]["templateRef"], layoutPlan=["intro"], visualReviewRequired=True))
            async with sessions() as session:
                task_id = (await session.get(PptWorkflowRef, started["taskRef"])).task_id
                task = await session.get(AsyncTaskModel, task_id)
                task.payload = {**task.payload, **({"deadline": 1} if stage == "deadline" else {"stage": stage})}
                if stage != "deadline":
                    task.status = AsyncTaskStatus.COMPLETED if stage == "completed" else AsyncTaskStatus.ERROR
                await session.commit()
            key = (str(client.owner), str(task_id))
            manager.sessions[key] = object()
            await renderer_lifecycle(sessions)
            assert manager.closed == [key] and manager.sessions == {}
            if stage == "deadline":
                async with sessions() as session:
                    task = await session.get(AsyncTaskModel, task_id)
                    assert task.status == AsyncTaskStatus.ERROR and task.payload["stage"] == "expired"
    asyncio.run(run())
