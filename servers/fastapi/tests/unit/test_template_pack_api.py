"""Real SQLite persistence and owner boundaries, isolated from user databases."""
import asyncio
from io import BytesIO
import uuid

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.auth.context import set_current_owner_id, reset_current_owner_id
from api.v1.ppt.endpoints.template_packs import (
    CompileRequest, DraftRequest, compile_trial, create_draft_version,
    get_pack, get_pack_asset, import_pptx, list_packs, validate_version,
)
from models.sql.template_pack_version import TemplatePackVersion
from models.sql.template_v2 import TemplateV2
from models.sql.user import User
from tests.unit.test_template_packs import source_pptx  # noqa: F401


def test_import_readback_versions_compile_and_foreign_owner(tmp_path, monkeypatch, source_pptx):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "assets"))
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path/'packs.db').as_posix()}")
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=[User.__table__, TemplateV2.__table__, TemplatePackVersion.__table__]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        first, second = uuid.uuid4(), uuid.uuid4()
        async with sessions() as session:
            session.add_all([User(id=identity, username=str(identity), hashed_password="unused") for identity in (first, second)])
            await session.commit()
        token = set_current_owner_id(first)
        try:
            async with sessions() as session:
                detail = await import_pptx(UploadFile(file=BytesIO(source_pptx), filename="sample.pptx"), session)
                original_source = detail["source"]
                assert (await get_pack(detail["id"], session))["source"] == original_source
                assert (await list_packs(session))["items"][0]["id"] == detail["id"]
                native = await compile_trial(detail["id"], CompileRequest(generationMode="template", ref="slide_1"), session)
                assert native["sourceHash"] == detail["sourceHash"]
                assert (await validate_version(detail["id"], session))["checks"]["structureAndCompile"] == "pass"
                updated = {**original_source, "name": "Updated draft"}
                draft = await create_draft_version(detail["id"], DraftRequest(expectedSourceHash=detail["sourceHash"], source=updated), session)
                assert draft["id"] != detail["id"] and draft["sourceHash"] != detail["sourceHash"]
                assert (await get_pack(detail["id"], session))["source"] == original_source
                assert (await session.scalar(select(TemplateV2).where(TemplateV2.id == detail["templateId"]))).layouts is None
                asset_id = original_source["assets"][0]["id"]
                assert (await get_pack_asset(detail["id"], asset_id, session)).headers["cache-control"] == "private, no-store"
            reset_current_owner_id(token)
            token = set_current_owner_id(second)
            async with sessions() as session:
                assert (await list_packs(session))["items"] == []
                for read in (lambda: get_pack(detail["id"], session), lambda: get_pack_asset(detail["id"], asset_id, session), lambda: compile_trial(detail["id"], CompileRequest(generationMode="template", ref="slide_1"), session)):
                    try:
                        await read()
                        raise AssertionError("Foreign owner obtained source or asset")
                    except HTTPException as failure:
                        assert failure.status_code == 404
        finally:
            reset_current_owner_id(token)
            await engine.dispose()
    asyncio.run(run())
