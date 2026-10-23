"""Per-request MCP timing without logging arguments, results or credentials."""

import asyncio
import logging
import re
from time import perf_counter
from uuid import uuid4

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext


LOGGER = logging.getLogger("presenton.mcp.timing")


def _log_name(value: str | None) -> str:
    # Names come from the client too; keep each event bounded and on one line.
    if not isinstance(value, str) or not re.fullmatch(r"[\w./-]{1,128}", value):
        return "unknown"
    return value


class MCPTimingMiddleware(Middleware):
    async def on_request(self, context: MiddlewareContext, call_next: CallNext):
        call_id = uuid4().hex
        method = _log_name(context.method)
        tool = (
            _log_name(getattr(context.message, "name", None))
            if context.method == "tools/call"
            else "-"
        )
        started = perf_counter()
        status = "error"
        error_type = "-"
        LOGGER.info(
            "[mcp.call.start] call_id=%s method=%s tool=%s", call_id, method, tool
        )
        try:
            result = await call_next(context)
            status = "error" if getattr(result, "is_error", False) else "success"
            return result
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            LOGGER.info(
                "[mcp.call.finish] call_id=%s method=%s tool=%s status=%s "
                "duration_ms=%.2f error_type=%s",
                call_id,
                method,
                tool,
                status,
                (perf_counter() - started) * 1000,
                error_type,
            )
