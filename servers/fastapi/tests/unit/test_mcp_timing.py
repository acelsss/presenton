import asyncio
import logging
import re
from types import SimpleNamespace

import httpx
import pytest
from fastmcp import Client
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult

from utils import mcp_timing


def timing_messages(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == mcp_timing.LOGGER.name
    ]


@pytest.mark.parametrize("outcome", ["success", "error", "cancelled", "error_result"])
def test_timing_preserves_result_and_failure_without_logging_payload(
    monkeypatch, caplog, outcome
):
    caplog.set_level(logging.INFO, logger=mcp_timing.LOGGER.name)
    ticks = iter([10.0, 10.125])
    monkeypatch.setattr(mcp_timing, "perf_counter", lambda: next(ticks))
    secret = "private-session-and-slide-content"
    context = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(name="agent_mutate_document", arguments={"data": secret}),
    )
    result = ToolResult(content=secret, is_error=outcome == "error_result")
    failure = asyncio.CancelledError() if outcome == "cancelled" else ValueError(secret)

    async def handler(received):
        assert received is context
        if outcome in {"error", "cancelled"}:
            raise failure
        return result

    async def run():
        if outcome in {"error", "cancelled"}:
            with pytest.raises(type(failure)) as caught:
                await mcp_timing.MCPTimingMiddleware().on_request(context, handler)
            assert caught.value is failure
        else:
            assert await mcp_timing.MCPTimingMiddleware().on_request(context, handler) is result

    asyncio.run(run())
    messages = timing_messages(caplog)
    assert len(messages) == 2
    assert "[mcp.call.start]" in messages[0]
    assert "[mcp.call.finish]" in messages[1]
    assert "tool=agent_mutate_document" in messages[1]
    assert f"status={'error' if outcome == 'error_result' else outcome}" in messages[1]
    assert "duration_ms=125.00" in messages[1]
    assert secret not in "\n".join(messages)
    ids = [re.search(r"call_id=([0-9a-f]{32})", message).group(1) for message in messages]
    assert ids[0] == ids[1]


def test_client_names_cannot_inject_multiline_log_events(caplog):
    caplog.set_level(logging.INFO, logger=mcp_timing.LOGGER.name)
    context = MiddlewareContext(
        method="tools/call", message=SimpleNamespace(name="unknown\nforged-log")
    )

    async def handler(_context):
        return None

    asyncio.run(mcp_timing.MCPTimingMiddleware().on_request(context, handler))
    assert all("tool=unknown" in message for message in timing_messages(caplog))
    assert all("\n" not in message for message in timing_messages(caplog))


@pytest.mark.parametrize("external", [True, False])
def test_server_times_each_concurrent_call_and_listing(monkeypatch, caplog, external):
    import mcp_server

    monkeypatch.setenv("PRESENTON_EXTERNAL_AGENT", str(external).lower())
    caplog.set_level(logging.INFO, logger=mcp_timing.LOGGER.name)

    async def run():
        async with httpx.AsyncClient(base_url="http://test") as api:
            server = mcp_server.create_mcp_server(api)
            entered = 0
            both_entered = asyncio.Event()

            @server.tool
            async def timing_probe(value: int) -> int:
                nonlocal entered
                entered += 1
                if entered == 2:
                    both_entered.set()
                await asyncio.wait_for(both_entered.wait(), timeout=5)
                return value

            async with Client(server) as client:
                await client.list_tools()
                results = await asyncio.gather(
                    client.call_tool("timing_probe", {"value": 1}),
                    client.call_tool("timing_probe", {"value": 2}),
                )
                assert [result.data for result in results] == [1, 2]

    asyncio.run(run())
    messages = timing_messages(caplog)
    assert any("method=tools/list" in message and "duration_ms=" in message for message in messages)
    calls = [message for message in messages if "tool=timing_probe" in message]
    assert len(calls) == 4
    starts = [message for message in calls if "[mcp.call.start]" in message]
    finishes = [message for message in calls if "[mcp.call.finish]" in message]
    start_ids = {re.search(r"call_id=([0-9a-f]{32})", message).group(1) for message in starts}
    finish_ids = {re.search(r"call_id=([0-9a-f]{32})", message).group(1) for message in finishes}
    assert len(start_ids) == 2 and start_ids == finish_ids
    assert all("status=success" in message and "duration_ms=" in message for message in finishes)
