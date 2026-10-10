"""Read-only SHA verification against the manifest shipped by the image build."""
import hashlib
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
report = {"checked": 0, "mismatches": [], "installedPythonCopies": 0}
for filename in sys.argv[2:]:
    manifest = json.loads(Path(filename).read_text())
    for relative, expected in manifest["files"].items():
        target = root / relative
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            report["mismatches"].append(relative)
        report["checked"] += 1
        if relative.startswith("servers/fastapi/") and target.suffix == ".py":
            for installed in Path("/opt/venv/lib").glob("python*/site-packages/" + relative.removeprefix("servers/fastapi/")):
                if hashlib.sha256(installed.read_bytes()).hexdigest() != expected:
                    report["mismatches"].append(str(installed))
                report["installedPythonCopies"] += 1
print(json.dumps(report))
sys.exit(bool(report["mismatches"]))
