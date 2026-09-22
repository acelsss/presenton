"""Opt-in, client-neutral API used by HTTP and the existing MCP server."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from api.v1.auth.principal import principal_from_request
from services.agent_tools.catalog import tool_catalog
from services.agent_tools.config import AGENT_INSTRUCTIONS, external_agent_mode
from services.agent_tools.schemas import (
    BatchMutation,
    CreateDocument,
    Mutation,
    OpenSession,
    ReadBatch,
    ToolRequest,
)
from services.agent_tools.service import AgentDocumentService
from services.chat.memory_layer import PresentationChatMemoryLayer
from services.chat.tools import ChatTools
from services.database import get_async_session


AGENT_TOOLS_ROUTER = APIRouter(prefix="/api/v1/agent-tools", tags=["External Agent"])


async def service(request: Request, session=Depends(get_async_session)):
    if not external_agent_mode():
        raise HTTPException(404, "External agent API is disabled")
    principal = principal_from_request(request)
    return AgentDocumentService(session, principal.user_id)


Service = Annotated[AgentDocumentService, Depends(service)]


async def caller(
    service: Service,
    token: Annotated[
        str, Header(alias="X-Presenton-Session", min_length=20, max_length=200)
    ],
):
    return await service.caller(token)


Caller = Annotated[object, Depends(caller)]


@AGENT_TOOLS_ROUTER.get("/capabilities", operation_id="agent_capabilities")
async def capabilities(service: Service):
    """Read the native tool catalog, protocol and supported workflow before writing."""
    tools = ChatTools(PresentationChatMemoryLayer(service.session, uuid.UUID(int=0)))
    return {
        "protocolVersion": "1.0",
        "instructions": AGENT_INSTRUCTIONS,
        "tools": tool_catalog(tools),
        "capabilities": {
            "persistentDocuments": True,
            "nativeTools": True,
            "batchReadTools": True,
            "atomicBatchMutations": True,
            "writerLeases": False,
            "uiProjection": True,
            "internalAgent": False,
            "internalMemory": False,
            "internalImageGeneration": False,
            "browserHandoff": False,
            "durableGeneration": False,
            "fixedRevisionExport": False,
            "hostBinding": False,
        },
    }


@AGENT_TOOLS_ROUTER.post("/sessions", operation_id="agent_open_session")
async def open_session(payload: OpenSession, service: Service):
    """Open a distinct caller session for one agent/tab; return its private session token."""
    return await service.open_session(payload.label)


@AGENT_TOOLS_ROUTER.post("/documents", operation_id="agent_create_document")
async def create_document(payload: CreateDocument, service: Service, caller: Caller):
    """Create an empty persistent V2 document with a preallocated idempotency key."""
    return await service.create(payload)


@AGENT_TOOLS_ROUTER.get("/documents/{document_id}", operation_id="agent_read_document")
async def read_document(document_id: uuid.UUID, service: Service, caller: Caller):
    """Read a consistent current-UI snapshot and revision; UI and notes are authoritative."""
    return await service.read(document_id)


@AGENT_TOOLS_ROUTER.get(
    "/operations/{operation_id}", operation_id="agent_read_operation"
)
async def read_operation(operation_id: uuid.UUID, service: Service, caller: Caller):
    """Resolve an uncertain mutation outcome before retrying."""
    result = await service.receipt(operation_id)
    if result is None:
        raise HTTPException(404, {"code": "operation_not_found"})
    return result


@AGENT_TOOLS_ROUTER.post(
    "/documents/{document_id}/read-tool", operation_id="agent_read_tool"
)
async def read_tool(
    document_id: uuid.UUID, payload: ToolRequest, service: Service, caller: Caller
):
    """Execute an advertised native read tool against one consistent document revision."""
    return await service.read_tool(document_id, payload)


@AGENT_TOOLS_ROUTER.post(
    "/documents/{document_id}/mutations", operation_id="agent_mutate_document"
)
async def mutate_document(
    document_id: uuid.UUID, payload: Mutation, service: Service, caller: Caller
):
    """Apply one native/lifecycle tool atomically with revision checking and receipt."""
    return await service.mutate(document_id, caller, payload)


@AGENT_TOOLS_ROUTER.post(
    "/documents/{document_id}/read-tools", operation_id="agent_read_tools"
)
async def read_tools(document_id: uuid.UUID, payload: ReadBatch, service: Service, caller: Caller):
    """Read 1-20 selected native tools in one request at one document revision.

    Prefer this to repeated agent_read_tool calls for selected layout schemas.
    Send only the layouts needed by your content, not every available layout.
    """
    return await service.read_tools(document_id, payload)


@AGENT_TOOLS_ROUTER.post(
    "/documents/{document_id}/mutation-batches", operation_id="agent_mutate_document_batch"
)
async def mutate_document_batch(
    document_id: uuid.UUID, payload: BatchMutation, service: Service, caller: Caller
):
    """Apply 1-20 native/lifecycle operations atomically with one receipt and revision.

    Prefer one batch for the confirmed outlines plus confirmOutline, then another
    for saveSlide calls plus completeDocument. Write saveSlide.content as a JSON
    string. Operations run in order; any rejection rolls back the entire batch.
    Resend the identical batch operationId/payload after an uncertain response.
    """
    return await service.mutate(document_id, caller, payload)


@AGENT_TOOLS_ROUTER.get("/templates", operation_id="agent_list_templates")
async def list_templates(service: Service, caller: Caller):
    """List accessible compiled V2 templates; select only after outline confirmation."""
    return await service.templates()
