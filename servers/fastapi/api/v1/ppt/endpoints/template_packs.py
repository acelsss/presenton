"""Source-backed template inspection; never publishes unverified imports."""
from copy import deepcopy
import hashlib
from pathlib import Path
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from jsonschema.exceptions import ValidationError as ContentValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from api.v1.auth.context import get_current_owner_id
from models.sql.template_pack_version import TemplatePackVersion
from models.sql.template_v2 import TemplateV2
from services.database import get_async_session
from templates.pack.compiler import PackCompiler, source_hash, validate_pack
from templates.pack.models import StrictModel, TemplatePack
from templates.pack.pptx_import import MAX_UPLOAD_BYTES, extract_pack
from utils.get_env import get_app_data_directory_env


TEMPLATE_PACK_ROUTER = APIRouter(prefix="/template-packs", tags=["Template packs"])


def owner():
    identity = get_current_owner_id()
    if identity is None:
        raise HTTPException(401, "An authenticated owner is required")
    return identity


async def version_for_owner(version_id: str, session: AsyncSession):
    identity = owner()
    row = await session.scalar(select(TemplatePackVersion).join(TemplateV2, TemplateV2.id == TemplatePackVersion.template_id).where(TemplatePackVersion.id == version_id, TemplatePackVersion.owner_id == identity, TemplateV2.owner_id == identity))
    if row is None:
        raise HTTPException(404, "Template pack version not found")
    return row


def asset_urls(row):
    return {item["id"]: f"/api/v1/ppt/template-packs/{row.id}/assets/{item['id']}" for item in row.source["assets"]}


def asset_path(row, asset):
    filename = asset["filename"]
    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise HTTPException(422, "Invalid asset filename")
    base = Path(row.asset_directory).resolve()
    path = (base / filename).resolve()
    if path.parent != base:
        raise HTTPException(422, "Invalid asset path")
    return path


def verify_assets(row):
    findings = []
    for asset in row.source["assets"]:
        path = asset_path(row, asset)
        if not path.is_file() or path.stat().st_size != asset["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            findings.append({"severity": "error", "code": "asset_hash_mismatch", "assetId": asset["id"]})
    return findings


def details(row):
    return {"id": row.id, "templateId": row.template_id, "version": row.version, "name": row.source["name"], "sourceHash": row.source_hash, "status": row.status, "source": row.source, "report": row.validation_report, "assetUrls": asset_urls(row)}


@TEMPLATE_PACK_ROUTER.get("")
async def list_packs(session: AsyncSession = Depends(get_async_session), page: Annotated[int, Query(ge=1)] = 1, page_size: Annotated[int, Query(ge=1, le=100)] = 100):
    identity = owner()
    scope = (TemplatePackVersion.owner_id == identity, TemplateV2.owner_id == identity)
    query = select(TemplatePackVersion).join(TemplateV2, TemplateV2.id == TemplatePackVersion.template_id).where(*scope)
    rows = (await session.scalars(query.order_by(TemplatePackVersion.created_at.desc()).offset((page-1)*page_size).limit(page_size))).all()
    total = await session.scalar(select(func.count()).select_from(TemplatePackVersion).join(TemplateV2, TemplateV2.id == TemplatePackVersion.template_id).where(*scope))
    return {"items": [{"id": row.id, "templateId": row.template_id, "name": row.source["name"], "version": row.version, "sourceHash": row.source_hash, "status": row.status, "counts": row.validation_report.get("counts", {})} for row in rows], "total": total, "page": page, "pageSize": page_size}


@TEMPLATE_PACK_ROUTER.get("/schema")
async def pack_schema():
    owner()
    return TemplatePack.model_json_schema()


@TEMPLATE_PACK_ROUTER.post("/import")
async def import_pptx(pptx_file: UploadFile = File(...), session: AsyncSession = Depends(get_async_session)):
    identity = owner()
    if not (pptx_file.filename or "").lower().endswith(".pptx"):
        raise HTTPException(422, "A PPTX file is required")
    data = await pptx_file.read(MAX_UPLOAD_BYTES + 1)
    pack_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        raw, blobs = await run_in_threadpool(extract_pack, data, pack_id=pack_id, version="1", filename=pptx_file.filename)
        source = TemplatePack.model_validate(raw).model_dump(mode="json")
        report = await run_in_threadpool(validate_pack, source)
    except Exception as exc:
        raise HTTPException(422, f"PPTX extraction failed: {type(exc).__name__}") from exc
    # Inspection drafts do not enter the certified legacy generation catalog.
    directory = Path(get_app_data_directory_env() or "/tmp/presenton") / "uploads" / "users" / str(identity) / "template-packs" / version_id
    directory.mkdir(parents=True, exist_ok=False)
    for asset in source["assets"]:
        (directory / asset["filename"]).write_bytes(blobs[asset["id"]])
    template = TemplateV2(id=pack_id, owner_id=identity, name=source["name"], description="三层模板导入草稿；需在检验工作台验收", layouts=None, assets={"templatePackVersion": version_id})
    row = TemplatePackVersion(id=version_id, template_id=pack_id, owner_id=identity, version="1", source_hash=source_hash(source), source=source, validation_report=report, asset_directory=str(directory))
    session.add(template)
    await session.flush()
    session.add(row)
    await session.commit()
    return details(row)


@TEMPLATE_PACK_ROUTER.get("/{version_id}")
async def get_pack(version_id: str, session: AsyncSession = Depends(get_async_session)):
    return details(await version_for_owner(version_id, session))


@TEMPLATE_PACK_ROUTER.post("/{version_id}/validate")
async def validate_version(version_id: str, session: AsyncSession = Depends(get_async_session)):
    row = await version_for_owner(version_id, session)
    report = await run_in_threadpool(validate_pack, row.source, asset_urls(row))
    asset_findings = await run_in_threadpool(verify_assets, row)
    report["findings"].extend(asset_findings)
    report["checks"]["assets"] = "fail" if asset_findings else "pass"
    if any(f.get("severity") == "error" for f in report["findings"]):
        report["checks"]["structureAndCompile"] = "fail"
    return report


class CompileRequest(StrictModel):
    generationMode: str
    ref: str | None = None
    themeRef: str | None = None
    root: dict | None = None
    content: dict | None = None


@TEMPLATE_PACK_ROUTER.post("/{version_id}/compile")
async def compile_trial(version_id: str, request: CompileRequest, session: AsyncSession = Depends(get_async_session)):
    row = await version_for_owner(version_id, session)
    try:
        compiler = PackCompiler(row.source, request.themeRef, asset_urls(row))
        result = await run_in_threadpool(compiler.compile, mode=request.generationMode, ref=request.ref, root=request.root, content=request.content)
        if request.generationMode == "template":
            result["contentSchema"] = compiler.content_schema(request.ref)
        return result
    except (ValueError, KeyError, TypeError, RecursionError, ContentValidationError) as exc:
        raise HTTPException(422, str(exc)[:2000]) from exc


@TEMPLATE_PACK_ROUTER.get("/{version_id}/assets/{asset_id}")
async def get_pack_asset(version_id: str, asset_id: str, session: AsyncSession = Depends(get_async_session)):
    row = await version_for_owner(version_id, session)
    asset = next((item for item in row.source["assets"] if item["id"] == asset_id), None)
    if asset is None:
        raise HTTPException(404, "Asset not found")
    path = asset_path(row, asset)
    if not path.is_file() or path.stat().st_size != asset["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
        raise HTTPException(409, "Asset changed or missing")
    return FileResponse(path, media_type=asset["mimeType"], filename=asset["filename"], headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


class DraftRequest(StrictModel):
    expectedSourceHash: str
    source: dict


@TEMPLATE_PACK_ROUTER.post("/{version_id}/versions")
async def create_draft_version(version_id: str, request: DraftRequest, session: AsyncSession = Depends(get_async_session)):
    row = await version_for_owner(version_id, session)
    if request.expectedSourceHash != row.source_hash:
        raise HTTPException(409, "Source hash differs")
    raw = deepcopy(request.source)
    if raw.get("assets") != row.source["assets"] or raw.get("id") != row.source["id"]:
        raise HTTPException(422, "Version editing cannot replace asset manifests or template identity")
    raw["version"] = str(uuid.uuid4())
    try:
        source = TemplatePack.model_validate(raw).model_dump(mode="json")
        report = await run_in_threadpool(validate_pack, source)
    except (ValueError, TypeError, RecursionError) as exc:
        raise HTTPException(422, str(exc)[:2000]) from exc
    created = TemplatePackVersion(template_id=row.template_id, owner_id=owner(), version=source["version"], source_hash=source_hash(source), source=source, validation_report=report, asset_directory=row.asset_directory)
    session.add(created)
    await session.commit()
    return details(created)
