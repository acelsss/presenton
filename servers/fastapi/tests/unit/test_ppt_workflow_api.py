import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from api.v1.ppt_workflow import PPT_WORKFLOW_ROUTER
from services.agent_tools.workflow_mcp import create_workflow_mcp, combined_mcp_app
from tests.unit.test_agent_tools import database, slide_content
from tests.unit.test_agent_tools_api import app_for_sessions


@asynccontextmanager
async def wire_client(app, path="/mcp"):
    """Exercise the deployed HTTP transport's negotiated session, not stdio fallback IDs."""
    from types import SimpleNamespace
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                    headers={"Accept": "application/json, text/event-stream"}) as http:
            counter = 0

            async def rpc(method, params):
                nonlocal counter
                counter += 1
                response = await http.post(path, json={"jsonrpc": "2.0", "id": counter, "method": method, "params": params})
                assert response.status_code == 200, response.text
                if response.headers.get("mcp-session-id"):
                    http.headers["mcp-session-id"] = response.headers["mcp-session-id"]
                return response.json()["result"]

            await rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
            await http.post(path, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

            async def list_tools():
                return [SimpleNamespace(**tool) for tool in (await rpc("tools/list", {}))["tools"]]

            async def call_tool(name, arguments, raise_on_error=True):
                result = await rpc("tools/call", {"name": name, "arguments": arguments})
                if raise_on_error:
                    assert not result.get("isError"), result
                return SimpleNamespace(data=result.get("structuredContent"), is_error=result.get("isError", False))

            yield SimpleNamespace(list_tools=list_tools, call_tool=call_tool)


@pytest.mark.parametrize("path", ["/mcp", "/mcp/workflow"])
def test_business_catalog_three_real_mcp_calls(tmp_path, monkeypatch, path):
    import mcp_server
    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    monkeypatch.setenv("DISABLE_AUTH", "false")

    async def run():
        async with database(tmp_path) as (sessions, owners):
            app = app_for_sessions(sessions, owners[0].owner, monkeypatch)
            app.include_router(PPT_WORKFLOW_ROUTER)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                          headers={"Authorization": "Bearer fixture-auth"}) as api:
                server = combined_mcp_app(mcp_server.create_legacy_agent_mcp(api),
                                          mcp_server.create_mcp_server(api), json_response=True)
                async with wire_client(server, path) as mcp:
                    catalog = await mcp.list_tools()
                    assert {t.name for t in catalog} == {"ppt_prepare", "ppt_start", "ppt_submit", "ppt_task", "ppt_read", "ppt_edit", "ppt_export"}
                    schema = json.dumps([t.inputSchema for t in catalog])
                    assert all(word not in schema for word in ("sessionToken", "writerEpoch", "operationId", "editRef", "X-Presenton-Client"))
                    preparation = (await mcp.call_tool("ppt_prepare", {"request": {
                        "title": "六页业务流程", "confirmed": True, "outline": [f"# Page {i}" for i in range(6)],
                    }})).data
                    result = (await mcp.call_tool("ppt_start", {"request": {
                        "preparationRef": preparation["preparationRef"],
                        "templateRef": preparation["templates"][0]["templateRef"], "layoutPlan": ["intro"] * 6,
                    }})).data
                    saved = (await mcp.call_tool("ppt_submit", {"request": {
                        "taskRef": result["taskRef"], "pages": [{"slotId": slot["slotId"], "content": slide_content(),
                             "speakerNote": "讲稿"} for slot in result["slots"]],
                    }})).data
                    assert saved["status"] == "completed", saved
                    assert len(saved["committed"]) == 6
                    other_server = create_workflow_mcp(api)
                    async with wire_client(other_server.http_app(path="/mcp", json_response=True)) as other:
                        failed = await other.call_tool("ppt_submit", {"request": {"taskRef": result["taskRef"], "pages": []}}, raise_on_error=False)
                        assert failed.is_error
    asyncio.run(run())


def test_default_alias_and_legacy_catalogs_preserve_auth_and_sessions(monkeypatch):
    import mcp_server
    from fastmcp.server.auth import AccessToken, TokenVerifier

    class Verifier(TokenVerifier):
        async def verify_token(self, token):
            if token == "fixture-key":
                return AccessToken(token=token, client_id="fixture-user", scopes=[])

    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    async def run():
        async with httpx.AsyncClient(base_url="http://test") as api:
            auth = Verifier()
            app = combined_mcp_app(mcp_server.create_legacy_agent_mcp(api, auth=auth),
                                   mcp_server.create_mcp_server(api, auth=auth), json_response=True,
                                   host_origin_protection=True, allowed_hosts=["localhost"],
                                   allowed_origins=["http://localhost"])
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http:
                    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
                    business_tools = {"ppt_prepare", "ppt_start", "ppt_submit", "ppt_task", "ppt_read", "ppt_edit", "ppt_export"}
                    for path in ("/mcp", "/mcp/", "/mcp/workflow", "/mcp/workflow/", "/mcp/legacy", "/mcp/legacy/"):
                        headers = {"Accept": "application/json, text/event-stream"}
                        assert (await http.post(path, json=body, headers=headers)).status_code == 401
                        headers["Authorization"] = "Bearer fixture-key"
                        response = await http.post(path, json=body, headers=headers)
                        assert response.status_code == 200, response.text
                        headers["mcp-session-id"] = response.headers["mcp-session-id"]
                        await http.post(path, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
                        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
                        # The alias must accept the same negotiated workflow session.
                        target = "/mcp" if "workflow" in path else path
                        response = await http.post(target, json=listing, headers=headers)
                        assert response.status_code == 200, response.text
                        names = {t["name"] for t in response.json()["result"]["tools"]}
                        if "legacy" in path:
                            assert len(names) == 10 and all(name.startswith("agent_") for name in names)
                        else:
                            assert names == business_tools
                            assert (await http.post("/mcp/legacy", json=listing, headers=headers)).status_code == 404
                        unsafe = await http.post(path, json=listing, headers={**headers, "Origin": "http://untrusted.example"})
                        assert unsafe.status_code == 403
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["auto", "legacy", "2026-07-28"])
def test_sdk_http_client_negotiates_one_actor_for_prepare_start_submit(tmp_path, monkeypatch, mode):
    import httpx2
    import mcp_server
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    monkeypatch.setenv("DISABLE_AUTH", "false")

    async def run():
        async with database(tmp_path) as (sessions, owners):
            api_app = app_for_sessions(sessions, owners[0].owner, monkeypatch)
            api_app.include_router(PPT_WORKFLOW_ROUTER)
            actors = []

            async def capture_actor(request):
                actors.append(request.headers.get("X-Presenton-Client"))

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app), base_url="http://test",
                                        headers={"Authorization": "Bearer fixture-auth"},
                                        event_hooks={"request": [capture_actor]}) as api:
                app = combined_mcp_app(mcp_server.create_legacy_agent_mcp(api),
                                       mcp_server.create_mcp_server(api), json_response=True)

                def factory(**kwargs):
                    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)

                def client():
                    return Client(StreamableHttpTransport("http://test/mcp", httpx_client_factory=factory),
                                  mode=mode, timeout=10)

                async with app.router.lifespan_context(app), client() as remote:
                    prepared = (await remote.call_tool("ppt_prepare", {"request": {
                        "title": "SDK 自动协商验收", "confirmed": True, "outline": ["# One", "# Two"],
                    }})).data
                    request = {"preparationRef": prepared["preparationRef"],
                               "templateRef": prepared["templates"][0]["templateRef"], "layoutPlan": ["intro"] * 2}
                    async with client() as other:
                        continued = (await other.call_tool("ppt_start", {"request": request})).data
                        assert continued["status"] == "awaiting_content"
                    result = (await remote.call_tool("ppt_start", {"request": request})).data
                    done = (await remote.call_tool("ppt_submit", {"request": {
                        "taskRef": result["taskRef"], "pages": [{"slotId": s["slotId"], "content": slide_content()}
                                                               for s in result["slots"]],
                    }})).data
                    assert done["status"] == "completed" and len(done["committed"]) == 2
                    assert len(actors) == 4
                    assert all(actor.startswith("mcp/") for actor in actors)
    asyncio.run(asyncio.wait_for(run(), 25))


@pytest.mark.parametrize("body", [b"Internal Server Error", b"[]", b'{"detail":"private diagnostic"}'])
def test_mcp_upstream_failure_is_safe_even_without_json(body):
    async def run():
        async def upstream(request):
            return httpx.Response(500, content=body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://test") as api:
            app = create_workflow_mcp(api).http_app(path="/mcp", json_response=True)
            async with wire_client(app) as client:
                result = await client.call_tool("ppt_task", {"request": {"taskRef": "t" * 32}}, raise_on_error=False)
                assert result.is_error
                # Check the public tool error instead of an incidental JSON parser exception.
                with pytest.raises(AssertionError, match="500: upstream_unavailable"):
                    await client.call_tool("ppt_task", {"request": {"taskRef": "t" * 32}})
    asyncio.run(run())


def test_long_poll_allows_worker_to_finish_without_updating_snapshot(tmp_path, monkeypatch):
    from tests.unit.test_ppt_workflow import WorkflowClient, start, pages
    from services.agent_tools import workflow_schemas as inputs
    from services.agent_tools.workflow_worker import drive
    from models.sql.ppt_workflow import PptWorkflowRef
    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", "true")
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            _, started = await start(client, 3)
            await client.call("submit", inputs.Submit(editRef=started["editRef"], pages=pages(started)))
            async with sessions() as session:
                task_id = (await session.get(PptWorkflowRef, started["taskRef"])).task_id
            app = app_for_sessions(sessions, client.owner, monkeypatch)
            app.include_router(PPT_WORKFLOW_ROUTER)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                        headers={"X-Presenton-Client": client.actor, "Authorization": "Bearer fixture-auth"}) as http:
                requests = [asyncio.create_task(http.post("/api/v1/agent-tools/workflow/task",
                    json={"taskRef": started["taskRef"], "waitSeconds": 3})) for _ in range(3)]
                await asyncio.sleep(0.1)
                await drive(sessions, client.owner, task_id)
                for response in await asyncio.gather(*requests):
                    assert response.status_code == 200, response.text
                    assert response.json()["status"] == "completed"
                    assert len(response.json()["committed"]) == 3
    asyncio.run(asyncio.wait_for(run(), 10))
