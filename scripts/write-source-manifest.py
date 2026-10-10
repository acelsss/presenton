"""Record copied build inputs and runtime files using only the Python stdlib."""
import hashlib
import json
from pathlib import Path
import sys

root, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
excluded = {".git", ".local", ".venv", "__pycache__", "node_modules", ".next-build",
            ".pytest_cache", ".ruff_cache", ".coverage", ".playwright-cli"}
files = {}
bases = sys.argv[3:]
if "--include-build-output" in bases:
    excluded.remove(".next-build")
    bases.remove("--include-build-output")
for base in bases:
    target = root / base
    candidates = target.rglob("*") if target.is_dir() else [target]
    for file in candidates:
        relative = file.relative_to(root)
        if file.is_file() and not excluded.intersection(relative.parts) and file.suffix != ".pyc":
            files[relative.as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
data = {"algorithm": "sha256", "files": dict(sorted(files.items()))}
output.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
print("Recorded", len(files), "build files")
