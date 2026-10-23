import asyncio
import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastmcp import Client as MCPClient

from api.middlewares import SessionAuthMiddleware, UserConfigEnvUpdateMiddleware
from api.v1.agent_tools import AGENT_TOOLS_ROUTER
from api.v1.auth.principal import AuthPrincipal
from models.sql.user import User
from services.database import get_async_session
from tests.unit.test_agent_tools import database


def app_for_sessions(sessions, owner, monkeypatch):
    import api.middlewares as middleware

    app = FastAPI()
    app.include_router(AGENT_TOOLS_ROUTER)
    from api.v1.ppt.router import API_V1_PPT_ROUTER
    from api.v2.router import API_V2_ROUTER
    app.include_router(API_V1_PPT_ROUTER)
    app.include_router(API_V2_ROUTER)

    async def get_session():
        async with sessions() as session:
            yield session

    async def principal(request, session):
        if request.headers.get("authorization") != "Bearer fixture-auth":
            return None, None
        return AuthPrincipal(
            user_id=owner, username="fixture", is_admin=False, method="jwt"
        ), await session.get(User, owner)

    app.dependency_overrides[get_async_session] = get_session
    monkeypatch.setattr(middleware, "async_session_maker", sessions)
    monkeypatch.setattr(middleware, "resolve_request_principal", principal)
    monkeypatch.setattr(
        middleware,
        "maybe_proxy_presenton_cloud_request",
        AsyncMock(side_effect=AssertionError("cloud proxy forbidden")),
    )
    monkeypatch.setattr(
        middleware,
        "update_env_with_user_config",
        lambda: pytest.fail("provider config must not load"),
    )
    app.add_middleware(UserConfigEnvUpdateMiddleware)
    app.add_middleware(SessionAuthMiddleware)
    return app


def test_http_and_mcp_use_same_sessions_documents_and_catalog(tmp_path, monkeypatch):
    import mcp_server

    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    monkeypatch.setenv("DISABLE_AUTH", "false")

    async def run():
        async with database(tmp_path) as (sessions, clients):
            app = app_for_sessions(sessions, clients[0].owner, monkeypatch)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                assert (
                    await api.get("/api/v1/agent-tools/capabilities")
                ).status_code == 401
                api.headers["Authorization"] = "Bearer fixture-auth"
                capabilities = (
                    await api.get("/api/v1/agent-tools/capabilities")
                ).json()
                assert capabilities["capabilities"]["writerLeases"] is False
                assert capabilities["capabilities"]["internalAgent"] is False
                assert capabilities["capabilities"]["internalMemory"] is False
                assert capabilities["capabilities"]["internalImageGeneration"] is False
                for method, path in [
                    ("POST", "/api/v1/ppt/presentation/generate/async"),
                    ("GET", "/api/v1/ppt/presentation/stream/" + str(uuid.uuid4())),
                    ("POST", "/api/v1/ppt/chat/message"),
                    ("GET", "/api/v1/ppt/images/generate"),
                    ("POST", "/api/v2/ppt/presentation/generate/smart/async"),
                ]:
                    assert (await api.request(method, path)).status_code == 409
                opened = (
                    await api.post(
                        "/api/v1/agent-tools/sessions", json={"label": "HTTP"}
                    )
                ).json()
                missing = await api.post(
                    "/api/v1/agent-tools/documents",
                    json={"operationId": str(uuid.uuid4()), "title": "missing session"},
                )
                assert missing.status_code == 422
                headers = {"X-Presenton-Session": opened["sessionToken"]}
                created = await api.post(
                    "/api/v1/agent-tools/documents",
                    headers=headers,
                    json={
                        "operationId": str(uuid.uuid4()),
                        "title": "HTTP 和 MCP 共用",
                    },
                )
                assert created.status_code == 200, created.text
                doc = created.json()["documentId"]
                mcp = mcp_server.create_legacy_agent_mcp(api)
                async with MCPClient(mcp) as remote:
                    tools = await remote.list_tools()
                    assert {tool.name for tool in tools} == {
                        "agent_capabilities",
                        "agent_open_session",
                        "agent_create_document",
                        "agent_read_document",
                        "agent_read_operation",
                        "agent_read_tool",
                        "agent_mutate_document",
                        "agent_mutate_document_batch",
                        "agent_read_tools",
                        "agent_list_templates",
                    }
                    assert "confirmOutline" in mcp.instructions
                    assert "not exposed yet" in mcp.instructions
                    # FastMCP converts the declared header parameter without custom transport glue.
                    mcp_session = await remote.call_tool(
                        "agent_open_session", {"label": "MCP"}
                    )
                    mcp_token = mcp_session.data["sessionToken"]
                    assert mcp_session.data["sessionId"] != opened["sessionId"]
                    envelope = {"document_id": doc, "X-Presenton-Session": mcp_token}
                    read = await remote.call_tool("agent_read_document", envelope)
                    assert read.data["documentId"] == doc and read.data["revision"] == 0
                    mutation = await remote.call_tool(
                        "agent_mutate_document",
                        {
                            **envelope,
                            "operationId": str(uuid.uuid4()),
                            "expectedRevision": 0,
                            "tool": "addOutline",
                            "arguments": {"content": "# MCP 保存", "index": None},
                        },
                    )
                    assert mutation.data["status"] == "applied"
                    http_read = (
                        await api.get(
                            f"/api/v1/agent-tools/documents/{doc}", headers=headers
                        )
                    ).json()
                    assert (
                        http_read["revision"] == 1
                        and len(http_read["outlines"]["slides"]) == 1
                    )
                    batched = await remote.call_tool(
                        "agent_mutate_document_batch",
                        {
                            **envelope,
                            "operationId": str(uuid.uuid4()),
                            "expectedRevision": 1,
                            "operations": [
                                {"tool": "addOutline", "arguments": {"content": "# 第二页"}},
                                {"tool": "confirmOutline", "arguments": {"confirmed": True}},
                            ],
                        },
                    )
                    assert batched.data["status"] == "applied"
                    assert batched.data["revision"] == 2
                    encoded_retry = await remote.call_tool(
                        "agent_mutate_document_batch",
                        {
                            **envelope,
                            "operationId": batched.data["operationId"],
                            "expectedRevision": 1,
                            "operations": json.dumps([
                                {"tool": "addOutline", "arguments": {"content": "# 第二页"}},
                                {"tool": "confirmOutline", "arguments": {"confirmed": True}},
                            ]),
                        },
                    )
                    assert encoded_retry.data["replayed"] and encoded_retry.data["revision"] == 2
                    read_batch = await remote.call_tool(
                        "agent_read_tools",
                        {
                            **envelope,
                            "operations": [
                                {"tool": "getSlideAtIndex", "arguments": {"index": 0, "includeFullContent": False}},
                                {"tool": "searchSlide", "arguments": {"query": "missing", "limit": 5}},
                            ],
                        },
                    )
                    assert read_batch.data["revision"] == 2
                    assert len(read_batch.data["results"]) == 2

    asyncio.run(run())


def test_external_mode_refuses_auth_disabled_and_skips_model_startup(
    monkeypatch, tmp_path
):
    import api.lifespan as lifecycle

    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    monkeypatch.setenv("DISABLE_AUTH", "true")

    async def refused():
        with pytest.raises(RuntimeError, match="requires authentication"):
            async with lifecycle.app_lifespan(FastAPI()):
                pytest.fail("must fail closed")

    asyncio.run(refused())
    monkeypatch.setenv("DISABLE_AUTH", "false")
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    blocked = AsyncMock(side_effect=AssertionError("model/provider startup forbidden"))
    monkeypatch.setattr(lifecycle, "migrate_provider_settings_from_file", blocked)
    monkeypatch.setattr(
        lifecycle, "check_llm_and_image_provider_api_or_model_availability", blocked
    )
    monkeypatch.setattr(
        lifecycle,
        "update_env_with_user_config",
        lambda: pytest.fail("provider config loaded"),
    )
    for name in [
        "migrate_database_on_startup",
        "create_db_and_tables",
        "bootstrap_database_admin",
        "fail_interrupted_async_tasks",
        "import_default_templates_on_startup",
        "dispose_engines",
    ]:
        monkeypatch.setattr(lifecycle, name, AsyncMock())

    async def allowed():
        async with lifecycle.app_lifespan(FastAPI()):
            pass
        assert blocked.await_count == 0

    asyncio.run(allowed())
