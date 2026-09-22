"""Business-only MCP catalog. Session identity is injected by the transport."""
from contextlib import asynccontextmanager
import json

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from starlette.applications import Starlette
from starlette.routing import Mount

from services.agent_tools import workflow_schemas as schemas
from utils.mcp_timing import MCPTimingMiddleware

INSTRUCTIONS = """Use external reasoning only. Presenton stores/renders your content; it never generates content or images.
After user outline confirmation: ppt_prepare -> ppt_start -> ppt_submit. Choose template/layouts from returned references.
ppt_start atomically creates the document, saves the confirmed outline,
and binds the selected template. It returns all selected layout schemas; no separate initialization tools are needed.
Pass structured content objects. Do not generate operation IDs, open caller sessions, or acquire/renew locks.
Use ppt_submit exportFormats or ppt_export for exports; no local Python export precheck or script is required.
Use taskRef for ppt_submit. On needs_input, submit only the listed erroneous/missing slots with the SAME taskRef; good candidates are retained.
For parallel draft subagents, assign distinct slots from ppt_start. Each submits its pages with expectedPageRevision
copied from that slot, finish=false and no exportFormats. Awaiting_content/staged means saved candidate, not a failed request.
After all children finish, the parent submits pages=[] with finish=true and exportFormats to finalize/export once.
On queued/saving/exporting, use ppt_task with waitSeconds=30 and the SAME taskRef, not another generation/export request.
On technical_error, report the returned error and stop polling. Resume only after its cause is fixed; do not loop resume.
On a lost response, replay the identical start/submit/edit/export request. The server deduplicates it.
Replayed status is current task status; receiptStatus/receiptRevision describe historical acceptance, not a rollback.
To edit completed slides: ppt_read(documentId, slideIds=[assigned IDs]) -> ppt_edit(documentId, pageEdits=[
{slideId, expectedPageRevision: returned pageRevision, operations}]). Omit operation indices; the server resolves each slideId.
Different pages have independent versions: parallel subagents do not conflict because another page changed.
On page_revision_conflict, reread ONLY that page. Never increment its version yourself or overwrite automatically.
Use whole-document operations+expectedRevision for structural changes such as adding/deleting slides.
The parent waits for all page edits, reads the final revision, then ppt_export(documentId, expectedRevision=revision, formats).
Never use ppt_start or ppt_submit to edit completed slides.
Current UI and speaker notes are authoritative; preserve unrelated edits. There are NO writer leases, expiry or client takeover.
Do not generate operation IDs or maintain editing tokens. Copy documentId/revision directly from tool results.
Only genuine needs_rebase/revision conflicts require a fresh read/business decision; never blindly overwrite.
Report success only for committed pages and completed export files.
When the task and requested export formats are completed, return their exact export URLs as clickable links and stop.
Default delivery is links only. Do not inspect slide previews, download files, run local export scripts,
search local/container paths, or use Docker/SQLite/curl to locate or verify artifacts.
Only inspect previews or edit/re-export when the user explicitly asks for inspection or changes.
visualReviewRequired is metadata that visual review has not been performed, not an instruction to perform it by default.
Do not claim visual inspection was completed when delivering links without inspection.
"""


def create_workflow_mcp(api_client, *, auth=None):
    server = FastMCP(name="Presenton Workflow", auth=auth, instructions=INSTRUCTIONS)

    async def call(name, request, ctx):
        # Client identity is diagnostic only; ownership comes from API auth.
        response = await api_client.post("/api/v1/agent-tools/workflow/" + name,
            json=request.model_dump(mode="json", by_alias=True),
            headers={"X-Presenton-Client": "mcp/" + ctx.session_id})
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            detail = body.get("detail", {}) if isinstance(body, dict) else {}
            fallback = "upstream_unavailable" if response.status_code >= 500 else "request_rejected"
            code = detail.get("code", fallback) if isinstance(detail, dict) else fallback
            guidance = {key: detail[key] for key in ("documentId", "slideId", "slotId", "nextTool", "currentRevision", "expectedRevision", "currentPageRevision", "message")
                        if isinstance(detail, dict) and key in detail}
            raise ToolError(f"{response.status_code}: {code}" + (" " + json.dumps(guidance, ensure_ascii=False) if guidance else ""))
        return response.json()

    @server.tool()
    async def ppt_prepare(request: schemas.Prepare, ctx: Context) -> dict:
        """Stage a confirmed outline and return pinned template/layout candidates; no document is created."""
        return await call("prepare", request, ctx)

    @server.tool()
    async def ppt_start(request: schemas.Start, ctx: Context) -> dict:
        """Create a document atomically with outline/template; return stable taskRef, slots and schemas."""
        return await call("start", request, ctx)

    @server.tool()
    async def ppt_submit(request: schemas.Submit, ctx: Context) -> dict:
        """Submit draft pages or repair listed slots using the same taskRef. Completed documents must use ppt_read/ppt_edit."""
        return await call("submit", request, ctx)

    @server.tool()
    async def ppt_task(request: schemas.Task, ctx: Context) -> dict:
        """Wait up to 30 seconds, resume persisted work, or cancel this same task without regenerating content."""
        return await call("task", request, ctx)

    @server.tool()
    async def ppt_read(request: schemas.Read, ctx: Context) -> dict:
        """Read current UI/notes and pageRevision per stable slideId; optionally select only a subagent's assigned slides."""
        return await call("read", request, ctx)

    @server.tool()
    async def ppt_edit(request: schemas.Edit, ctx: Context) -> dict:
        """Edit assigned pages with pageEdits (slideId, expectedPageRevision, operations). Different pages do not conflict. No leases."""
        return await call("edit", request, ctx)

    @server.tool()
    async def ppt_export(request: schemas.Export, ctx: Context) -> dict:
        """Queue export of the read revision including its frozen UI, notes, theme and local asset bytes."""
        return await call("export", request, ctx)

    server.add_middleware(MCPTimingMiddleware())
    return server


def combined_mcp_app(legacy, workflow, **http_options):
    old_app = legacy.http_app(path="/mcp/legacy", **http_options)
    new_app = workflow.http_app(path="/mcp", **{**http_options, "stateless_http": False})

    class Dispatch:
        async def __call__(self, scope, receive, send):
            path = scope.get("path", "").rstrip("/")
            target = old_app if path == "/mcp/legacy" else new_app
            if path in {"/mcp", "/mcp/workflow", "/mcp/legacy"}:
                # The workflow alias shares one transport/session manager. Avoid
                # redirects: clients must retain their POST body and MCP session.
                canonical = "/mcp/legacy" if path == "/mcp/legacy" else "/mcp"
                scope = {**scope, "path": canonical, "raw_path": canonical.encode("ascii")}
            await target(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app):
        async with old_app.lifespan(app), new_app.lifespan(app):
            yield

    return Starlette(routes=[Mount("/", app=Dispatch())], lifespan=lifespan)
