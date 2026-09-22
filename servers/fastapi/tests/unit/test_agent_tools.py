import asyncio
import copy
import json
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.agent_document import AgentDocument
from models.sql.image_asset import ImageAsset
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.user import User
from services import database as database_events
from services.agent_tools.catalog import tool_catalog
from services.agent_tools.schemas import (
    CreateDocument,
    Mutation,
    ToolRequest,
)
from services.agent_tools.service import AgentDocumentService
from services.chat.execution_policy import project_slide_ui
from services.chat.memory_layer import PresentationChatMemoryLayer
from services.chat.tools import ChatTools
from tests.unit.test_slide_ui_chat_tools import _slide_ui


class Client:
    def __init__(self, sessions, owner):
        self.sessions, self.owner, self.token = sessions, owner, None

    async def call(self, method, *args):
        async with self.sessions() as session:
            service = AgentDocumentService(session, self.owner)
            if method == "open_session":
                response = await service.open_session(*args)
                self.token = response["sessionToken"]
                return response
            caller = await service.caller(self.token)
            if method == "mutate":
                return await getattr(service, method)(args[0], caller, args[1])
            return await getattr(service, method)(*args)


@asynccontextmanager
async def database(tmp_path):
    test_url = os.environ.get("PRESENTON_TEST_POSTGRES_URL")
    admin_engine = None
    if test_url:
        if not test_url.startswith("postgresql+asyncpg://"):
            raise ValueError("PRESENTON_TEST_POSTGRES_URL must use postgresql+asyncpg")
        schema = "agent_test_" + uuid.uuid4().hex
        admin_engine = create_async_engine(test_url)
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(
            test_url, connect_args={"server_settings": {"search_path": schema}}
        )
    else:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{(tmp_path / 'agent.db').as_posix()}"
        )
        event.listen(
            engine.sync_engine, "connect", database_events._enable_sqlite_foreign_keys
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    owners = [uuid.uuid4(), uuid.uuid4()]
    async with sessions() as session:
        session.add_all(
            [
                User(id=owner, username=str(owner), hashed_password="unused")
                for owner in owners
            ]
        )
        session.add(
            TemplateV2(
                id="test-template",
                name="Native V2",
                is_default=True,
                layouts={"layouts": [_slide_ui()]},
            )
        )
        await session.commit()
    try:
        yield sessions, [Client(sessions, owner) for owner in owners]
    finally:
        await engine.dispose()
        if admin_engine is not None:
            async with admin_engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin_engine.dispose()


async def create(client):
    await client.call("open_session", "test-client")
    result = await client.call(
        "create", CreateDocument(operationId=uuid.uuid4(), title="持久稿件")
    )
    doc = uuid.UUID(result["documentId"])
    return doc, None


async def mutate(client, doc, epoch, tool, arguments, *, revision=None, operation=None):
    if revision is None:
        revision = (await client.call("read", doc))["revision"]
    return await client.call(
        "mutate",
        doc,
        Mutation(
            operationId=operation or uuid.uuid4(),
            expectedRevision=revision,
            writerEpoch=epoch,
            tool=tool,
            arguments=arguments,
        ),
    )


async def prepare(client):
    doc, epoch = await create(client)
    assert (
        await mutate(
            client, doc, epoch, "addOutline", {"content": "# 第一页", "index": None}
        )
    )["status"] == "applied"
    assert (await mutate(client, doc, epoch, "confirmOutline", {"confirmed": True}))[
        "status"
    ] == "applied"
    assert (
        await mutate(
            client, doc, epoch, "selectTemplate", {"templateId": "test-template"}
        )
    )["status"] == "applied"
    return doc, epoch


def slide_content(title="原始标题"):
    return {
        "hero": {"Title": title},
        "body": {"Bullets": ["保留列表内容"]},
        "__speaker_note__": "讲稿",
    }


async def save(client, doc, epoch, content, *, replace=False):
    return await mutate(
        client,
        doc,
        epoch,
        "saveSlide",
        {
            "index": 0,
            "layoutId": "intro",
            "content": json.dumps(content),
            "replaceOldSlideAtIndex": replace,
        },
    )


def test_native_schema_catalog_and_current_ui_projection():
    catalog = tool_catalog(ChatTools(PresentationChatMemoryLayer(None, uuid.uuid4())))
    schemas = {tool["name"]: tool for tool in catalog}
    assert "generateAssets" not in schemas
    assert "generateAssets" not in json.dumps(catalog)
    assert "createComponent" in schemas
    assert (
        schemas["saveSlide"]["inputSchema"]["properties"]["layoutId"]["minLength"] == 1
    )
    ui = _slide_ui()
    ui["components"][0]["elements"][0]["text"] = "画布上的新标题"
    assert project_slide_ui(ui)[0]["content"]["text"] == "画布上的新标题"


@pytest.mark.parametrize("component_tool", ["addComponent", "createComponent"])
def test_remaining_advertised_structure_tools_use_the_same_coordinator(
    tmp_path, component_tool
):
    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await prepare(client)
            await save(client, doc, epoch, slide_content())
            await mutate(client, doc, epoch, "completeDocument", {})
            component = copy.deepcopy(_slide_ui()["components"][0])
            component["id"] = "extra"
            added = await mutate(
                client,
                doc,
                epoch,
                component_tool,
                {"index": 0, "component": json.dumps(component)},
            )
            assert added["status"] == "applied", added
            moved = await mutate(
                client,
                doc,
                epoch,
                "updateComponent",
                {"index": 0, "componentId": "extra", "position": {"x": 400, "y": 400}},
            )
            assert moved["status"] == "applied", moved
            element = copy.deepcopy(component["elements"][0])
            added_element = await mutate(
                client,
                doc,
                epoch,
                "addElement",
                {"index": 0, "componentId": "extra", "element": json.dumps(element)},
            )
            assert added_element["status"] == "applied", added_element
            deleted_element = await mutate(
                client,
                doc,
                epoch,
                "deleteElement",
                {"index": 0, "elementPath": "components[2].elements[1]"},
            )
            assert deleted_element["status"] == "applied", deleted_element
            deleted = await mutate(
                client,
                doc,
                epoch,
                "deleteComponent",
                {"index": 0, "componentId": "extra"},
            )
            assert deleted["status"] == "applied", deleted
            blank = await mutate(client, doc, epoch, "addNewSlide", {"index": None})
            assert blank["status"] == "applied", blank
            assert len((await client.call("read", doc))["slides"]) == 2
            deleted_slide = await mutate(
                client, doc, epoch, "deleteSlide", {"index": 1}
            )
            assert deleted_slide["status"] == "applied", deleted_slide
            assert len((await client.call("read", doc))["slides"]) == 1

    asyncio.run(run())


def test_native_workflow_without_models_persists_current_ui_and_stable_ids(
    tmp_path, monkeypatch
):
    import services.chat.memory_layer as native

    model = AsyncMock(side_effect=AssertionError("internal model must not run"))
    monkeypatch.setattr(
        native,
        "ImageGenerationService",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("image service initialized")
        ),
    )
    monkeypatch.setattr(
        native.MEM0_PRESENTATION_MEMORY_SERVICE, "store_generated_outlines", model
    )
    monkeypatch.setattr(
        native.MEM0_PRESENTATION_MEMORY_SERVICE, "store_slide_edit", model
    )

    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await prepare(client)
            assert (await save(client, doc, epoch, slide_content()))[
                "status"
            ] == "applied"
            assert (await mutate(client, doc, epoch, "completeDocument", {}))[
                "status"
            ] == "applied"
            initial = await client.call("read", doc)
            slide_id = initial["slides"][0]["slideId"]
            # Replacement stays on the same manuscript/page identity.
            assert (
                await save(
                    client, doc, epoch, slide_content("第二个标题"), replace=True
                )
            )["status"] == "applied"
            assert (await client.call("read", doc))["slides"][0]["slideId"] == slide_id
            snapshot = await client.call("read", doc)
            unchanged = copy.deepcopy(snapshot["slides"][0]["ui"]["components"][1])
            title = "中文软长度提示" * 30
            edit = await mutate(
                client,
                doc,
                epoch,
                "updateElement",
                {"index": 0, "elementPath": "components[0].elements[0]", "text": title},
            )
            assert edit["status"] == "applied", edit
            latest = await client.call("read", doc)
            assert latest["slides"][0]["projection"][0]["content"]["text"] == title
            assert latest["slides"][0]["speakerNote"] == "讲稿"
            assert latest["slides"][0]["ui"]["components"][1] == unchanged
            found = await client.call(
                "read_tool",
                doc,
                ToolRequest(tool="searchSlide", arguments={"query": title, "limit": 5}),
            )
            assert found["revision"] == latest["revision"]
            assert found["result"]["count"] == 1
            missing = await client.call(
                "read_tool",
                doc,
                ToolRequest(
                    tool="getSlideAtIndex",
                    arguments={"index": 1, "includeFullContent": True},
                ),
            )
            assert missing["result"]["found"] is False
            assert model.await_count == 0

    asyncio.run(run())


def test_create_and_write_receipts_noop_and_key_conflict(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, clients):
            client = clients[0]
            await client.call("open_session", "one")
            request = CreateDocument(operationId=uuid.uuid4(), title="去重")
            first = await client.call("create", request)
            replay = await client.call("create", request)
            assert first["documentId"] == replay["documentId"] and replay["replayed"]
            with pytest.raises(HTTPException) as caught:
                await client.call(
                    "create", request.model_copy(update={"title": "不同内容"})
                )
            assert caught.value.detail["code"] == "operation_id_reused"
            doc = uuid.UUID(first["documentId"])
            epoch = None
            op = uuid.uuid4()
            args = {"content": "# 一", "index": None}
            initial = await mutate(
                client, doc, epoch, "addOutline", args, revision=0, operation=op
            )
            retry = await mutate(
                client, doc, epoch, "addOutline", args, revision=0, operation=op
            )
            assert retry["replayed"] and initial["revision"] == retry["revision"] == 1
            noop = await mutate(
                client, doc, epoch, "updateOutline", {"index": 0, "content": "# 一"}
            )
            assert noop["status"] == "noop" and noop["revision"] == 1
            async with sessions() as session:
                AgentDocumentService(session, client.owner)
                assert len(list(await session.scalars(select(PresentationModel)))) == 1

    asyncio.run(run())


def test_ownership_sessions_and_revision_without_writer_leases(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, clients):
            owner, other = clients
            doc, epoch = await create(owner)
            await other.call("open_session", "other-owner")
            with pytest.raises(HTTPException) as caught:
                await other.call("read", doc)
            assert caught.value.status_code == 404
            other.token = owner.token
            with pytest.raises(HTTPException) as caught:
                await other.call("read", doc)
            assert caught.value.status_code == 401
            second = Client(sessions, owner.owner)
            await second.call("open_session", "second-client")
            operation = uuid.uuid4()
            first = await mutate(second, doc, epoch, "addOutline", {"content": "# Saved"},
                                 operation=operation, revision=0)
            assert first["revision"] == 1
            with pytest.raises(HTTPException, match="revision_conflict"):
                await mutate(owner, doc, epoch, "addOutline", {"content": "# Stale"}, revision=0)
            retry = await mutate(owner, doc, epoch, "addOutline", {"content": "# Saved"},
                                 operation=operation, revision=0)
            assert retry["replayed"]
            assert (await mutate(owner, doc, epoch, "addOutline", {"content": "# Next"}, revision=1))["revision"] == 2
            assert "writer" not in await owner.call("read", doc)
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["false_result", "exception", "receipt_failure"])
def test_partial_native_changes_never_commit(tmp_path, monkeypatch, failure):
    async def run():
        async with database(tmp_path) as (sessions, clients):
            client = clients[0]
            doc, epoch = await create(client)
            original = ChatTools.execute_validated

            async def faulty(self, name, arguments):
                result = await original(self, name, arguments)
                if failure == "exception":
                    raise RuntimeError("simulated crash after flush")
                if failure == "false_result":
                    return {
                        "saved": False,
                        "message": "native rejection after mutation",
                    }
                return result

            monkeypatch.setattr(ChatTools, "execute_validated", faulty)
            if failure == "receipt_failure":

                async def fail_receipt(*args, **kwargs):
                    raise RuntimeError("simulated receipt failure")

                monkeypatch.setattr(AgentDocumentService, "_record", fail_receipt)
            op = uuid.uuid4()
            if failure == "false_result":
                rejected = await mutate(
                    client,
                    doc,
                    epoch,
                    "addOutline",
                    {"index": None, "content": "# rollback"},
                    operation=op,
                )
                assert rejected["status"] == "rejected" and rejected["revision"] == 0
            else:
                with pytest.raises(RuntimeError):
                    await mutate(
                        client,
                        doc,
                        epoch,
                        "addOutline",
                        {"index": None, "content": "# rollback"},
                        operation=op,
                    )
            snapshot = await client.call("read", doc)
            assert snapshot["outlines"] == {"slides": []} and snapshot["revision"] == 0
            receipt = await client.call("receipt", op)
            assert (receipt is not None) == (failure == "false_result")

    asyncio.run(run())


def test_lifecycle_invalid_targets_and_native_business_rejection(tmp_path):
    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await create(client)
            assert (
                await mutate(
                    client,
                    doc,
                    epoch,
                    "selectTemplate",
                    {"templateId": "test-template"},
                )
            )["status"] == "rejected"
            await mutate(
                client, doc, epoch, "addOutline", {"content": "# one", "index": None}
            )
            await mutate(client, doc, epoch, "confirmOutline", {"confirmed": True})
            await mutate(
                client, doc, epoch, "selectTemplate", {"templateId": "test-template"}
            )
            assert (await mutate(client, doc, epoch, "completeDocument", {}))[
                "status"
            ] == "rejected"
            rejected = await save(client, doc, epoch, {"missing_required_fields": True})
            assert (
                rejected["status"] == "rejected"
                and rejected["result"]["saved"] is False
            )
            assert (await client.call("read", doc))["slides"] == []
            out_of_range = await mutate(
                client,
                doc,
                epoch,
                "saveSlide",
                {
                    "index": 99,
                    "layoutId": "intro",
                    "content": json.dumps(slide_content()),
                    "replaceOldSlideAtIndex": False,
                },
            )
            assert out_of_range["status"] == "rejected"
            assert (await save(client, doc, epoch, slide_content()))[
                "status"
            ] == "applied"

    asyncio.run(run())


def test_native_queries_can_read_but_uncoordinated_writes_cannot_change_managed_documents(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, clients):
            client = clients[0]
            doc, epoch = await prepare(client)
            await save(client, doc, epoch, slide_content())
            async with sessions() as session:
                assert await session.get(PresentationModel, doc) is not None
                assert list(
                    await session.scalars(
                        select(SlideModel).where(SlideModel.presentation == doc)
                    )
                )
                result = await session.execute(
                    update(PresentationModel)
                    .where(PresentationModel.id == doc)
                    .values(title="bypass")
                )
                assert result.rowcount == 0
                result = await session.execute(
                    delete(SlideModel).where(SlideModel.presentation == doc)
                )
                assert result.rowcount == 0
                await session.commit()
            async with sessions() as session:
                session.add(
                    SlideModel(
                        presentation=doc,
                        layout_group="x",
                        layout="x",
                        index=9,
                        content={},
                    )
                )
                with pytest.raises(HTTPException) as caught:
                    await session.commit()
                assert (
                    caught.value.detail["code"]
                    == "managed_document_requires_coordinator"
                )
            assert len((await client.call("read", doc))["slides"]) == 1

    asyncio.run(run())


def test_existing_asset_survives_first_save_replacement_and_element_edit(
    tmp_path, monkeypatch
):
    import services.chat.memory_layer as native
    from utils.asset_directory_utils import normalize_slide_asset_url

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(
        native,
        "ImageGenerationService",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("generation forbidden")),
    )

    async def run():
        async with database(tmp_path) as (sessions, clients):
            client = clients[0]
            image_path = (
                tmp_path / "images" / "users" / str(client.owner) / "existing.png"
            )
            image_path.parent.mkdir(parents=True)
            # A local fixture; binding only checks existence/ownership, upload validates bytes.
            image_path.write_bytes(b"owned-image-fixture")
            url = normalize_slide_asset_url(str(image_path))
            async with sessions() as session:
                session.add(
                    ImageAsset(
                        owner_id=client.owner, path=str(image_path), is_uploaded=True
                    )
                )
                template = await session.get(TemplateV2, "test-template")
                layout = _slide_ui()
                layout["components"].append(
                    {
                        "id": "photo",
                        "description": "Existing asset",
                        "position": {"x": 300, "y": 40},
                        "elements": [
                            {
                                "type": "image",
                                "name": "Photo",
                                "decorative": False,
                                "is_icon": False,
                                "position": {"x": 0, "y": 0},
                                "size": {"width": 100, "height": 100},
                                "data": url,
                            }
                        ],
                    }
                )
                template.layouts = {"layouts": [layout]}
                await session.commit()
            doc, epoch = await prepare(client)
            content = {
                **slide_content(),
                "photo": {
                    "Photo": {
                        "image_prompt": "must never replace the supplied photo",
                        "image_url": url,
                    }
                },
            }
            first = await save(client, doc, epoch, content)
            assert first["status"] == "applied", first
            snapshot = await client.call("read", doc)
            slide_id = snapshot["slides"][0]["slideId"]
            assert (
                snapshot["slides"][0]["ui"]["components"][2]["elements"][0]["data"]
                == url
            )
            content["hero"]["Title"] = "换布局填充仍保留图片"
            content.pop("__speaker_note__")
            assert (await save(client, doc, epoch, content, replace=True))[
                "status"
            ] == "applied"
            assert (await client.call("read", doc))["slides"][0][
                "speakerNote"
            ] == "讲稿"
            edit = await mutate(
                client,
                doc,
                epoch,
                "updateElement",
                {
                    "index": 0,
                    "elementPath": "components[2].elements[0]",
                    "text": url,
                    "position": {"x": 5, "y": 6},
                },
            )
            assert edit["status"] == "applied", edit
            latest = await client.call("read", doc)
            assert latest["slides"][0]["slideId"] == slide_id
            assert (
                latest["slides"][0]["ui"]["components"][2]["elements"][0]["data"] == url
            )
            bad = await mutate(
                client,
                doc,
                epoch,
                "updateElement",
                {
                    "index": 0,
                    "elementPath": "components[2].elements[0]",
                    "text": "https://untrusted.invalid/image.png",
                },
            )
            assert bad["status"] == "rejected"
            prompt_only = await mutate(
                client,
                doc,
                epoch,
                "updateElement",
                {
                    "index": 0,
                    "elementPath": "components[2].elements[0]",
                    "text": "generate a different photo",
                },
            )
            assert prompt_only["status"] == "rejected"
            assert (await client.call("read", doc))["slides"] == latest["slides"]
            image_path.unlink()
            assert (await save(client, doc, epoch, content, replace=True))[
                "status"
            ] == "rejected"

    asyncio.run(run())


def test_imported_template_decoration_survives_native_save(tmp_path, monkeypatch):
    from pathlib import Path

    from services.agent_tools.assets import ExistingAssets, asset_references
    from services.agent_tools.errors import OperationRejected
    from templates.default_templates import (
        _copy_default_template_static_assets,
        _load_default_template,
    )

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    template_dir = Path(__file__).resolve().parents[4] / "templates" / "general"
    template = _load_default_template(template_dir)
    _copy_default_template_static_assets(template_dir, template.id)
    template.id = "test-template"

    async def run():
        async with database(tmp_path) as (sessions, clients):
            client = clients[0]
            async with sessions() as session:
                existing = await session.get(TemplateV2, "test-template")
                existing.layouts = template.layouts
                session.add(existing)
                await session.commit()
            doc, epoch = await prepare(client)
            result = await mutate(
                client,
                doc,
                epoch,
                "saveSlide",
                {
                    "index": 0,
                    "layoutId": "table_of_contents",
                    "replaceOldSlideAtIndex": False,
                    "content": json.dumps(
                        {
                            "centered_title_block": {
                                "main_heading": "Existing template assets"
                            },
                            "table_of_content_items_grid": {
                                "items_grid": [
                                    {
                                        "item_number": str(i),
                                        "item_label": "Overview",
                                        "page_number": str(i),
                                    }
                                    for i in range(1, 6)
                                ]
                            },
                        }
                    ),
                },
            )
            assert result["status"] == "applied", result
            ui = (await client.call("read", doc))["slides"][0]["ui"]
            references = list(asset_references(ui))
            assert references and all(
                ref.startswith("/app_data/templates/general/static/")
                for ref in references
            )
            async with sessions() as session:
                assets = await ExistingAssets.for_document(session, client.owner, ui)
                await assets.validate(ui)
                unselected = (
                    tmp_path / "templates" / "another" / "static" / "unselected.png"
                )
                unselected.parent.mkdir(parents=True)
                unselected.write_bytes(b"existing-but-not-selected")
                for reference in (
                    "/app_data/templates/another/static/unselected.png",
                    "/app_data/templates/general/static/../../private.png",
                    f"/app_data/images/users/{clients[1].owner}/private.png",
                    "https://untrusted.invalid/image.png",
                ):
                    with pytest.raises(OperationRejected):
                        await assets.validate({"type": "image", "data": reference})
                remote = {
                    "type": "image",
                    "data": "https://untrusted.invalid" + references[0],
                }
                remote_assets = await ExistingAssets.for_document(
                    session, client.owner, remote
                )
                with pytest.raises(OperationRejected):
                    await remote_assets.validate(remote)

    asyncio.run(run())
