"""Freeze authorized bytes before exporting; never fetch a mutable editor URL."""
import base64
import copy
import hashlib
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import tempfile

from fastapi import HTTPException

from api.v1.auth.context import set_current_owner_id, reset_current_owner_id
from services.agent_tools.assets import ExistingAssets, asset_references
from services.agent_tools.service import fail
from services.agent_tools.workflow_timing import timed_stage
from services.export_task_service import ExportTaskService
from utils.asset_directory_utils import resolve_app_path_to_filesystem, get_exports_directory

LOGGER = logging.getLogger(__name__)
RENDER_PHASES = {"snapshot", "slide_html", "slide_dom", "preview_dom", "preview_assets",
                 "preview_capture", "export_pptx", "export_pdf"}


def render_error(value):
    """Only fixed diagnostic codes cross the renderer boundary, never page text."""
    if not isinstance(value, dict):
        value = {}
    result = {"code": value.get("code") if value.get("code") in {
        "snapshot_render_timeout", "snapshot_render_failed"} else "snapshot_render_failed"}
    if value.get("phase") in RENDER_PHASES:
        result["phase"] = value["phase"]
    if isinstance(value.get("slideIndex"), int) and 0 <= value["slideIndex"] < 1000:
        result["slideIndex"] = value["slideIndex"]
    return result


def snapshot_directory(owner_id, task_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", task_id):
        fail(500, "invalid_snapshot_task")
    token = set_current_owner_id(owner_id)
    try:
        return Path(get_exports_directory()) / "workflow" / task_id / "snapshots"
    finally:
        reset_current_owner_id(token)


def store_snapshot(owner_id, task_id, snapshot):
    """Persist immutable input bytes once; SQL stores only the content digest.

    Like rendered files, snapshots belong to the owner's shared app-data volume.
    Publish the file before committing its reference. A rolled-back transaction
    can leave an unreferenced file, but never a reference to partially written bytes.
    """
    directory = snapshot_directory(owner_id, task_id)
    directory.mkdir(parents=True, exist_ok=True)
    data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()
    fingerprint = hashlib.sha256(data).hexdigest()
    descriptor, temporary = tempfile.mkstemp(prefix="snapshot-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / f"{fingerprint}.json")
    finally:
        Path(temporary).unlink(missing_ok=True)
    return fingerprint


def load_snapshot(owner_id, task_id, fingerprint):
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        fail(422, "export_snapshot_invalid")
    try:
        data = (snapshot_directory(owner_id, task_id) / f"{fingerprint}.json").read_bytes()
    except OSError:
        fail(422, "export_snapshot_missing")
    if hashlib.sha256(data).hexdigest() != fingerprint:
        fail(422, "export_snapshot_changed")
    return json.loads(data)


def asset_fingerprints(owner_id, value):
    """Pin mutable local dependencies without silently substituting new bytes."""
    token = set_current_owner_id(owner_id)
    result = {}
    total = 0
    try:
        for url in set(ref for ref in asset_references(value) if isinstance(ref, str)):
            if not url.startswith(("/app_data/", "/static/", "/vendor/fonts/")):
                continue  # Native validation decides whether other references are usable.
            path = resolve_app_path_to_filesystem(url)
            if not path or not Path(path).is_file():
                result[url] = None
                continue
            size = Path(path).stat().st_size
            if size > 32 * 1024 * 1024 or total + size > 64 * 1024 * 1024:
                fail(422, "export_snapshot_asset_budget_exceeded")
            total += size
            with open(path, "rb") as stream:
                result[url] = hashlib.file_digest(stream, "sha256").hexdigest()
        return result
    finally:
        reset_current_owner_id(token)


def check_asset_fingerprints(owner_id, manifest):
    current = asset_fingerprints(owner_id, [{"url": url} for url in manifest])
    if current != manifest:
        fail(409, "pinned_asset_changed")


@timed_stage
async def freeze_snapshot(service, document_id, expected_assets=None):
    snapshot = await service.read(document_id)
    _, presentation = await service.document(document_id)
    expected_assets = {**(presentation.layout or {}).get("_agentAssetHashes", {}), **(expected_assets or {})}
    check_asset_fingerprints(service.owner_id, expected_assets)
    assets = await ExistingAssets.for_document(service.session, service.owner_id, presentation.layout)
    await assets.validate([slide["ui"] for slide in snapshot["slides"]])
    token = set_current_owner_id(service.owner_id)
    cache = {}
    total = 0

    def asset(value):
        nonlocal total
        if value in cache:
            return cache[value]
        if not isinstance(value, str) or not value.startswith(("/app_data/", "/static/", "/vendor/fonts/")):
            fail(422, "export_asset_must_be_local_and_owned")
        path = resolve_app_path_to_filesystem(value)
        if not path or not Path(path).is_file():
            fail(422, "export_asset_missing")
        size = Path(path).stat().st_size
        if size > 32 * 1024 * 1024 or total + size > 64 * 1024 * 1024:
            fail(422, "export_snapshot_asset_budget_exceeded")
        data = Path(path).read_bytes()
        if value in expected_assets and hashlib.sha256(data).hexdigest() != expected_assets[value]:
            fail(409, "pinned_asset_changed")
        total += len(data)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        cache[value] = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
        return cache[value]

    def walk(value):
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: walk(item) for key, item in value.items()}
        for key in ("url", "image_url", "icon_url", "__image_url__", "__icon_url__"):
            if result.get(key):
                result[key] = asset(result[key])
        if result.get("type") == "image":
            result["data"] = asset(result["data"])
        return result

    try:
        snapshot = copy.deepcopy(snapshot)
        snapshot["slides"] = [{"index": slide["index"], "slideId": slide["slideId"],
                               "ui": walk(slide["ui"]), "speakerNote": slide["speakerNote"]}
                              for slide in snapshot["slides"]]
        snapshot["theme"] = walk(snapshot.get("theme"))
        snapshot["fonts"] = {name: asset(url) for name, url in (presentation.fonts or {}).items()}
        return snapshot
    finally:
        reset_current_owner_id(token)


@timed_stage
async def render_snapshot(owner_id, task_id, claim_id, snapshot, formats):
    token = set_current_owner_id(owner_id)
    try:
        directory = Path(get_exports_directory()) / "workflow" / task_id / claim_id
        directory.mkdir(parents=True, exist_ok=True)
        runtime = ExportTaskService(timeout_seconds=300)
        result = await runtime._run_task({"type": "snapshot-export", "snapshot": snapshot,
                                           "formats": formats, "outputDirectory": str(directory)},
                                          "Snapshot renderer produced no result")
        for item in result.get("phases", []):
            if (item.get("phase") in RENDER_PHASES and item.get("status") in {"success", "error"}
                    and isinstance(item.get("durationMs"), (int, float))):
                LOGGER.info("[ppt.render] task=%s phase=%s status=%s duration_ms=%s",
                            task_id, item["phase"], item["status"], item["durationMs"])
        if result.get("error"):
            raise HTTPException(500, render_error(result["error"]))
        # Paths are derived here, never accepted from a client or child response.
        from utils.get_env import get_app_data_directory_env
        root = Path(get_app_data_directory_env()).resolve()
        prefix = "/app_data/" + directory.resolve().relative_to(root).as_posix()
        exports = {}
        for format in formats:
            path = directory / f"presentation.{format}"
            if result.get("formats", {}).get(format) == "completed" and path.is_file() and path.stat().st_size:
                exports[format] = {"status": "completed", "url": f"{prefix}/presentation.{format}",
                                   "revision": snapshot["revision"]}
            else:
                exports[format] = {"status": "error", **render_error(result.get("errors", {}).get(format))}
        previews = [f"{prefix}/preview-{i + 1}.png" for i in range(len(snapshot["slides"]))
                    if (directory / f"preview-{i + 1}.png").is_file()]
        return {"exports": exports, "previews": previews,
                "geometry": f"{prefix}/geometry.json", "visualReviewRequired": True}
    finally:
        reset_current_owner_id(token)
