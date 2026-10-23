"""Task-owned Node/Chromium. No database sessions cross this boundary."""
import asyncio
import json
import os
from pathlib import Path
import signal
import uuid

from api.v1.auth.context import set_current_owner_id, reset_current_owner_id
from services.agent_tools.service import fail
from services.agent_tools.workflow_export import snapshot_directory
from services.export_task_service import ExportTaskService, _windows_hidden_subprocess_kwargs

ALLOWED_ENV = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
               "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL", "FONTCONFIG_PATH", "FONTCONFIG_FILE",
               "APP_DATA_DIRECTORY", "TEMP_DIRECTORY", "PUPPETEER_EXECUTABLE_PATH",
               "PUPPETEER_CACHE_DIR", "PUPPETEER_TMP_DIR", "ASSETS_BASE_URL"}


class RenderSession:
    def __init__(self, process, release):
        self.process, self.release = process, release
        self.lock = asyncio.Lock()
        self.closed = False
        self.browser_pid = None

    async def request(self, payload):
        async with self.lock:
            if self.closed or self.process.returncode is not None:
                fail(503, "snapshot_renderer_unavailable")
            request_id = uuid.uuid4().hex
            message = json.dumps({**payload, "requestId": request_id}).encode() + b"\n"
            if len(message) > 64 * 1024:
                fail(422, "render_request_too_large")
            self.process.stdin.write(message)
            await self.process.stdin.drain()
            try:
                line = await asyncio.wait_for(self.process.stdout.readline(), 60)
                result = json.loads(line)
                if result.get("requestId") != request_id:
                    raise ValueError("unexpected renderer response")
                return result
            except BaseException:
                await self.close()
                raise

    async def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.process.returncode is None:
                if self.process.stdin:
                    self.process.stdin.close()
                try:
                    await asyncio.wait_for(self.process.wait(), 5)
                except TimeoutError:
                    self.process.terminate()
                    try:
                        await asyncio.wait_for(self.process.wait(), 2)
                    except TimeoutError:
                        self.process.kill()
                        await self.process.wait()
            # Chromium is launched in its own process group by Puppeteer.
            if os.name != "nt":
                for pid in (self.browser_pid, self.process.pid):
                    if pid:
                        try:
                            os.killpg(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
        finally:
            self.release()


class GenerationRenderers:
    def __init__(self):
        self.sessions = {}
        self.capacity = asyncio.Semaphore(4)

    async def _start(self, owner_id, task_id):
        await self.capacity.acquire()
        session = None
        token = set_current_owner_id(owner_id)
        try:
            runtime = ExportTaskService()
            root = snapshot_directory(owner_id, task_id).parent
            await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
            environment = {key: value for key, value in runtime._build_node_env().items() if key.upper() in ALLOWED_ENV}
            process = await asyncio.create_subprocess_exec(runtime.node_binary,
                str(Path(runtime.export_dir) / "render-session.mjs"), str(root), str(os.getpid()),
                cwd=runtime.export_dir, env=environment, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=128 * 1024, **({"start_new_session": True} if os.name != "nt" else _windows_hidden_subprocess_kwargs()))
            session = RenderSession(process, self.capacity.release)
            ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
            if ready.get("ready") is not True or not isinstance(ready.get("browserPid"), int):
                fail(503, "snapshot_renderer_unavailable")
            session.browser_pid = ready["browserPid"]
            return session
        except BaseException:
            if session:
                await session.close()
            else:
                self.capacity.release()
            raise
        finally:
            reset_current_owner_id(token)

    async def get(self, owner_id, task_id):
        key = (str(owner_id), str(task_id))
        pending = self.sessions.get(key)
        if pending and pending.done() and (pending.cancelled() or pending.exception()
                or pending.result().closed):
            self.sessions.pop(key)
            pending = None
        if pending is None:
            pending = asyncio.create_task(self._start(owner_id, task_id))
            self.sessions[key] = pending
        return await asyncio.shield(pending)

    async def close(self, owner_id, task_id):
        pending = self.sessions.pop((str(owner_id), str(task_id)), None)
        if pending is None:
            return
        if not pending.done():
            pending.cancel()
        try:
            session = await pending
        except (asyncio.CancelledError, Exception):
            return
        await session.close()

    async def close_all(self):
        await asyncio.gather(*(self.close(*key) for key in list(self.sessions)))


generation_renderers = GenerationRenderers()
