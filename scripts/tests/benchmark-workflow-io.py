"""Contended SQLite workflow transactions and event-loop heartbeat in isolated storage."""
import asyncio
import json
import os
from pathlib import Path
import statistics
import sys
import time
import shutil

source = Path(__file__).resolve().parents[2] / "servers" / "fastapi"
sys.path.insert(0, str(source))
root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)
os.environ["APP_DATA_DIRECTORY"] = str(root)
os.environ["DATABASE_URL"] = "sqlite:///" + (root / "imports.db").as_posix()
os.environ.pop("PRESENTON_TEST_POSTGRES_URL", None)

from sqlalchemy import event
from sqlalchemy.orm import Session
from services.agent_tools import workflow_schemas as inputs
from services.agent_tools.workflow_worker import drive
from models.sql.ppt_workflow import PptWorkflowRef
from tests.unit.test_agent_tools import database
from tests.unit.test_ppt_workflow import WorkflowClient, start, pages


async def main():
    held, wait, heartbeat = [], [], []
    stop = asyncio.Event()
    async def beat():
        expected = time.perf_counter()
        while not stop.is_set():
            expected += .005
            await asyncio.sleep(max(0, expected-time.perf_counter()))
            heartbeat.append(max(0, time.perf_counter()-expected)*1000)
    async with database(root) as (sessions, owners):
        engine = sessions.kw["bind"].sync_engine
        def before(conn, cursor, statement, parameters, context, many):
            if statement.upper().startswith("BEGIN IMMEDIATE"):
                conn.info["requested"] = time.perf_counter()
        def after(conn, cursor, statement, parameters, context, many):
            if statement.upper().startswith("BEGIN IMMEDIATE"):
                started = time.perf_counter()
                wait.append((started-conn.info.pop("requested"))*1000)
                conn.info["held"] = started
        def remember(session, transaction, conn):
            if not transaction.nested:
                session.info["measured_connection"] = conn
        def end(session):
            if session.in_nested_transaction():
                return
            conn = session.info.pop("measured_connection", None)
            if conn is None:
                return
            started = conn.info.pop("held", None)
            if started is not None:
                held.append((time.perf_counter()-started)*1000)
        event.listen(engine, "before_cursor_execute", before)
        event.listen(engine, "after_cursor_execute", after)
        event.listen(Session, "after_begin", remember)
        event.listen(Session, "after_commit", end)
        event.listen(Session, "after_rollback", end)
        client = WorkflowClient(sessions, owners[0].owner)
        resources = "--large-resources" in sys.argv
        snapshot = None
        if resources:
            import random, struct, zlib
            from services.agent_tools.workflow_resources import freeze_resources
            from services.agent_tools.workflow_export import store_snapshot
            width, height = 2048, 1024
            pixels = random.Random(0).randbytes(width * height * 3)
            raw = b"".join(b"\0" + pixels[y*width*3:(y+1)*width*3] for y in range(height))
            def chunk(kind, data):
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind+data))
            png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
            owner = str(client.owner)
            image = root / "images" / "users" / owner / "noise.png"
            font = root / "fonts" / "users" / owner / "font.ttf"
            image.parent.mkdir(parents=True, exist_ok=True); font.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(png)
            font_source = max((source.parent / "nextjs" / "public" / "vendor" / "fonts").rglob("*.ttf"), key=lambda p:p.stat().st_size)
            shutil.copyfile(font_source, font)
            snapshot = {"slides": [{"ui": {"type": "image", "data": "/app_data/" + image.relative_to(root).as_posix()}}],
                        "fonts": {"benchmark": "/app_data/" + font.relative_to(root).as_posix()}}
            async def freeze(index):
                task = f"large-resource-{index}"
                frozen = await asyncio.to_thread(freeze_resources, client.owner, task, snapshot)
                await asyncio.to_thread(store_snapshot, client.owner, task, frozen)
        _, task = await start(client, 20)
        async with sessions() as session:
            task_id = (await session.get(PptWorkflowRef, task["taskRef"])).task_id
        held.clear(); wait.clear()
        ticker = asyncio.create_task(beat())
        limit = asyncio.Semaphore(4)
        async def submit(candidate):
            async with limit:
                await client.call("submit", inputs.Submit(taskRef=task["taskRef"], pages=[candidate]))
        await asyncio.gather(*(submit(page) for page in pages(task)), *(freeze(i) for i in range(4)) if resources else [])
        await drive(sessions, client.owner, task_id, budget_seconds=30)
        stop.set(); await ticker
    p95 = lambda values: sorted(values)[max(0, int(len(values)*.95)-1)]
    report = {"pages":20,"submitConcurrency":4,"writeTransactions":len(held),
              "writerHeldP50Ms":statistics.median(held),"writerHeldP95Ms":p95(held),
              "writerWaitP95Ms":p95(wait),"heartbeatMaxMs":max(heartbeat),
              "scope":"SQLite BEGIN IMMEDIATE acquired through ORM after_commit/after_rollback; includes commit syscall; 20 native fixture pages",
              "concurrentResourceFreezes": 4 if resources else 0,
              "imageBytes": image.stat().st_size if resources else 0,
              "fontBytes": font.stat().st_size if resources else 0}
    (root / "workflow-io-result.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))

asyncio.run(main())
