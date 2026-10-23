"""20 paired old embedded-font cold renders vs generation sessions; isolated files only."""
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

root, task_file, baseline_file = map(Path, sys.argv[1:4])
root.mkdir(parents=True, exist_ok=True)
(root / "resources").mkdir(exist_ok=True)
original = json.loads(task_file.read_text())
snapshot = original["snapshot"]
manifest = {}
def pin(value):
    if isinstance(value, dict):
        return {k: pin(v) for k, v in value.items()}
    if isinstance(value, list):
        return [pin(v) for v in value]
    if not isinstance(value, str) or not value.startswith("data:") or ";base64," not in value:
        return value
    import base64
    header, encoded = value.split(",", 1)
    data = base64.b64decode(encoded)
    digest = hashlib.sha256(data).hexdigest()
    (root / "resources" / digest).write_bytes(data)
    manifest[digest] = {"file": digest, "mime": header[5:].split(";", 1)[0], "size": len(data)}
    return "https://ppt-assets.invalid/" + digest
compact = pin(snapshot)
compact["assetManifest"] = manifest
raw = json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode()
digest = hashlib.sha256(raw).hexdigest()
(root / "snapshots").mkdir(exist_ok=True)
snapshot_path = root / "snapshots" / (digest + ".json")
snapshot_path.write_bytes(raw)
env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "APP_DATA_DIRECTORY": "/app_data",
       "PUPPETEER_EXECUTABLE_PATH": "/usr/bin/chromium"}
# Resolve dependencies against the installed export runtime for the old immutable source.
prepare = '''import fs from "node:fs/promises"; import {createRequire} from "node:module";
const require=createRequire("/app/presentation-export/package.json");
let source=await fs.readFile(process.argv[1],"utf8");
source=source.replace('from "puppeteer"','from '+JSON.stringify(require.resolve("puppeteer")))
.replace('from "@presenton/export-core"','from '+JSON.stringify(require.resolve("@presenton/export-core")));
source+='\\nconst task=JSON.parse(await fs.readFile(process.argv[2],"utf8")); task.outputDirectory=process.argv[3]; task.formats=[];\\nconsole.log(JSON.stringify(await renderSnapshot(task,{browserLaunchOptions:{executablePath:"/usr/bin/chromium"}})));';
await fs.writeFile(process.argv[2],source);'''
old_module = root / "baseline-render.mjs"
subprocess.run(["node", "--input-type=module", "-e", prepare, str(baseline_file), str(old_module)], check=True, env=env)
cold_task = root / "cold-task.json"
cold_task.write_text(json.dumps({"snapshot": snapshot, "formats": []}))
begin = time.perf_counter()
worker = subprocess.Popen(["node", "/app/presentation-export/render-session.mjs", str(root), str(os.getpid())],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
ready = json.loads(worker.stdout.readline())
startup_ms = (time.perf_counter() - begin) * 1000
assert ready.get("ready"), ready
rows = []
try:
    # Warm resources once; the first request is reported separately.
    for index in range(21):
        row = {"index": index}
        for mode in (["cold", "warm"] if index % 2 else ["warm", "cold"]):
            out = root / (mode + "-" + str(index))
            out.mkdir(exist_ok=True)
            begin = time.perf_counter()
            if mode == "cold":
                completed = subprocess.run(["node", str(old_module), str(cold_task), str(out)], capture_output=True, text=True, env=env, timeout=60, check=True)
                response = json.loads(completed.stdout.splitlines()[-1])
            else:
                worker.stdin.write(json.dumps({"requestId": str(index), "type": "snapshot-export", "previewOnly": True,
                    "snapshotPath": str(snapshot_path), "snapshotHash": digest, "formats": [], "outputDirectory": str(out)}) + "\n")
                worker.stdin.flush()
                response = json.loads(worker.stdout.readline())
            row[mode] = {"wallMs": round((time.perf_counter() - begin) * 1000, 2), "phases": response.get("phases")}
            assert not response.get("error"), response
            row[mode]["pngSha256"] = hashlib.sha256((out / "preview-1.png").read_bytes()).hexdigest()
            if mode == "warm":
                assert not (out / "presentation.html").exists()
        assert row["cold"]["pngSha256"] == row["warm"]["pngSha256"], row
        rows.append(row)
        print(json.dumps({"pair": index, "coldMs": row["cold"]["wallMs"], "warmMs": row["warm"]["wallMs"], "samePng": True}), flush=True)
finally:
    worker.stdin.close()
    worker.wait(timeout=10)
values = lambda mode: sorted(row[mode]["wallMs"] for row in rows[1:])
cold, warm = values("cold"), values("warm")
report = {"rows": rows, "startupMs": round(startup_ms,2), "pids": ready,
    "snapshotBytes": len(raw), "oldSnapshotBytes": cold_task.stat().st_size, "resourceBytes": sum(item["size"] for item in manifest.values()),
    "coldP50Ms": statistics.median(cold), "warmP50Ms": statistics.median(warm), "warmP95Ms": warm[18],
    "improvementPercent": round((1-statistics.median(warm)/statistics.median(cold))*100,2),
    "noProcessRemaining": worker.poll() is not None}
(root / "benchmark-result.json").write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!="rows"}), flush=True)
