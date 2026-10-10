"""Exercise the installed renderer in an isolated Linux container, including parent loss."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

sys.path.insert(0, "/app/servers/fastapi")


def descendants(parent):
    rows = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            rows[int(entry.name)] = {"ppid": int(stat[1]), "state": stat[0], "start": stat[19]}
        except (FileNotFoundError, ProcessLookupError):
            continue
    found = {parent}
    while True:
        added = {pid for pid, row in rows.items() if row["ppid"] in found}
        if added <= found:
            break
        found |= added
    return {pid: rows[pid] for pid in found if pid in rows}


async def child():
    from services.agent_tools.generation_renderer import GenerationRenderers
    manager = GenerationRenderers()
    owner, task = str(uuid.uuid4()), uuid.uuid4().hex
    first, repeated = await asyncio.gather(manager.get(owner, task), manager.get(owner, task))
    assert first is repeated
    print(json.dumps({"pythonPid": os.getpid(), "nodePid": first.process.pid,
                      "browserPid": first.browser_pid, "descendants": descendants(first.process.pid)}), flush=True)
    action = sys.argv[1]
    if action == "normal":
        await manager.close(owner, task)
    elif action == "shutdown":
        await manager.close_all()
    elif action == "node_term":
        first.process.terminate()
        await first.process.wait()
        await manager.close_all()
    else:
        await asyncio.sleep(120)


def verify():
    rows = []
    for action in ("normal", "shutdown", "node_term", "parent_kill"):
        process = subprocess.Popen([sys.executable, __file__, "--child", action],
                                   stdout=subprocess.PIPE, text=True)
        ready = json.loads(process.stdout.readline())
        tracked = {int(pid): row for pid, row in ready["descendants"].items()}
        begin = time.monotonic()
        if action == "parent_kill":
            process.kill()
        process.wait(timeout=15)
        assert process.returncode == (-signal.SIGKILL if action == "parent_kill" else 0), action
        alive, zombies = [], []
        for _ in range(100):
            alive, zombies = [], []
            for pid, original in tracked.items():
                try:
                    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                except FileNotFoundError:
                    continue
                if stat[19] != original["start"]:
                    continue
                (zombies if stat[0] == "Z" else alive).append(pid)
            if not alive:
                break
            time.sleep(.1)
        row = {"action": action, "exitMs": round((time.monotonic()-begin)*1000, 2),
               "alive": alive, "zombies": zombies, "deduplicated": True, **ready}
        rows.append(row)
        assert not alive, row
        assert row["exitMs"] < 10000, row
        print(json.dumps({key: row[key] for key in ("action", "exitMs", "alive", "zombies", "deduplicated")}), flush=True)
    Path(os.environ["APP_DATA_DIRECTORY"], "lifecycle-result.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        sys.argv.pop(1)
        asyncio.run(child())
    else:
        verify()
