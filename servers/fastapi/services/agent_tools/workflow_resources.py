"""Freeze owned local resources once, without embedding them in JSON."""
import copy
import hashlib
import mimetypes
import os
from pathlib import Path
import tempfile
import re

from api.v1.auth.context import set_current_owner_id, reset_current_owner_id
from services.agent_tools.service import fail
from services.agent_tools.workflow_export import snapshot_directory
from utils.asset_directory_utils import resolve_app_path_to_filesystem

ASSET_ORIGIN = "https://ppt-assets.invalid"


def freeze_resources(owner_id, task_id, snapshot, expected=None):
    expected = expected or {}
    root = snapshot_directory(owner_id, task_id).parent / "resources"
    root.mkdir(parents=True, exist_ok=True)
    cache, manifest = {}, {}
    token = set_current_owner_id(owner_id)
    total = 0

    def asset(url):
        nonlocal total
        if url in cache:
            return cache[url]
        if not isinstance(url, str) or not url.startswith(("/app_data/", "/static/", "/vendor/fonts/")):
            fail(422, "export_asset_must_be_local_and_owned")
        resolved = resolve_app_path_to_filesystem(url)
        if not resolved or not Path(resolved).is_file():
            fail(422, "export_asset_missing")
        source = Path(resolved)
        mime = mimetypes.guess_type(source)[0] or "application/octet-stream"
        suffix = source.suffix.lower()
        if not suffix or not suffix[1:].isalnum() or len(suffix) > 12:
            suffix = ".bin"
        pinned = expected.get(url)
        target = root / (pinned + suffix) if isinstance(pinned, str) and re.fullmatch(r"[a-f0-9]{64}", pinned) else None
        # An existing pinned task file was already frozen; Node verifies it on first use.
        if target is None or not target.is_file():
            data = source.read_bytes()
            checksum = hashlib.sha256(data).hexdigest()
            if pinned is not None and pinned != checksum:
                fail(409, "pinned_asset_changed")
            target = root / (checksum + suffix)
            if len(data) > 32 * 1024 * 1024:
                fail(422, "export_snapshot_asset_budget_exceeded")
            fd, temporary = tempfile.mkstemp(dir=root, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o444)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        checksum = target.name[:-len(suffix)]
        size = target.stat().st_size
        total += size
        if size > 32 * 1024 * 1024 or total > 64 * 1024 * 1024:
            fail(422, "export_snapshot_asset_budget_exceeded")
        manifest[checksum] = {"file": target.name, "mime": mime, "size": size}
        cache[url] = ASSET_ORIGIN + "/" + checksum
        return cache[url]

    def walk(value):
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: walk(item) for key, item in value.items()}
        for key in ("url", "image_url", "icon_url", "__image_url__", "__icon_url__"):
            if result.get(key):
                result[key] = asset(result[key])
        # Read snapshots also carry tool/schema metadata with type="image".
        # Only concrete UI image nodes contain bytes or a local data reference.
        if result.get("type") == "image" and isinstance(result.get("data"), str) and result["data"]:
            result["data"] = asset(result["data"])
        return result

    try:
        result = walk(copy.deepcopy(snapshot))
        result["fonts"] = {name: asset(url) for name, url in snapshot.get("fonts", {}).items()}
        result["assetManifest"] = manifest
        return result
    finally:
        reset_current_owner_id(token)
