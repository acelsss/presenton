"""HTTP and MCP share these business operations and authenticated ownership."""
import asyncio
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from api.v1.auth.principal import principal_from_request
from services.agent_tools.config import external_agent_mode
from services.agent_tools.service import fail
from services.agent_tools.workflow import WorkflowService
from services.agent_tools import workflow_schemas as schemas
from services.database import get_async_session
from utils.mcp_public_urls import absolute_mcp_url

PPT_WORKFLOW_ROUTER = APIRouter(prefix="/api/v1/agent-tools/workflow", tags=["PPT Workflow"])


async def service(request: Request,
                  client: Annotated[str, Header(alias="X-Presenton-Client", max_length=200)] = "http",
                  session=Depends(get_async_session)):
    if not external_agent_mode():
        fail(404, "workflow_disabled")
    return WorkflowService(session, principal_from_request(request).user_id, client)


Service = Annotated[WorkflowService, Depends(service)]


def public_result(result):
    # Old reference inputs remain readable for upgrades. New clients use stable
    # task IDs or document/revision pairs, never consumable editing handles.
    return {key: value for key, value in result.items() if key not in {"editRef", "repairRef"}}


def result_links(request, result):
    result = public_result(result)
    result["exports"] = {format: {**item, **({"url": absolute_mcp_url(request, item["url"])} if item.get("url") else {})}
                         for format, item in result.get("exports", {}).items()}
    result["previews"] = [absolute_mcp_url(request, url) for url in result.get("previews", [])]
    return result


@PPT_WORKFLOW_ROUTER.post("/prepare")
async def prepare(payload: schemas.Prepare, service: Service):
    return await service.prepare(payload)


@PPT_WORKFLOW_ROUTER.post("/start")
async def start(payload: schemas.Start, service: Service, request: Request):
    result = await service.start(payload)
    from services.agent_tools.workflow_worker import wake_worker
    wake_worker()
    return result_links(request, result)


@PPT_WORKFLOW_ROUTER.post("/submit")
async def submit(payload: schemas.Submit, service: Service, request: Request):
    result = await service.submit(payload)
    from services.agent_tools.workflow_worker import wake_worker
    wake_worker()
    return result_links(request, result)


@PPT_WORKFLOW_ROUTER.post("/preview")
async def preview(payload: schemas.Preview, service: Service):
    return await service.preview(payload)


@PPT_WORKFLOW_ROUTER.post("/visual_read")
async def visual_read(payload: schemas.VisualRead, service: Service):
    from services.agent_tools.workflow_visual import visual_read as read
    return await read(service, payload)


@PPT_WORKFLOW_ROUTER.post("/visual_ack")
async def visual_ack(payload: schemas.VisualAck, service: Service):
    from services.agent_tools.workflow_visual import visual_ack as ack
    return await ack(service, payload)


@PPT_WORKFLOW_ROUTER.post("/visual_review")
async def visual_review(payload: schemas.VisualReview, service: Service):
    from services.agent_tools.workflow_visual import visual_review as review
    return await review(service, payload)


@PPT_WORKFLOW_ROUTER.post("/task")
async def task(payload: schemas.Task, service: Service, request: Request):
    result = await service.task(payload)
    deadline = time.monotonic() + payload.wait_seconds
    while result["status"] in {"queued", "saving", "export_queued", "exporting"} and time.monotonic() < deadline:
        await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))
        result = await service.task(payload.model_copy(update={"action": "status"}))
    return result_links(request, result)


@PPT_WORKFLOW_ROUTER.post("/read")
async def read(payload: schemas.Read, service: Service):
    return public_result(await service.read_for_edit(payload))


@PPT_WORKFLOW_ROUTER.post("/edit")
async def edit(payload: schemas.Edit, service: Service):
    return public_result(await service.edit(payload))


@PPT_WORKFLOW_ROUTER.post("/export")
async def export(payload: schemas.Export, service: Service, request: Request):
    return result_links(request, await service.export(payload))
