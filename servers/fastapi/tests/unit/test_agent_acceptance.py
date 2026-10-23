"""Issue #2 T22/T24/T26: durable effects, native results and existing images."""

import asyncio
import copy
import hashlib
import json
import uuid

import pytest
from PIL import Image

from models.sql.image_asset import ImageAsset
from models.sql.template_v2 import TemplateV2
from services.chat.tools import ChatTools
from tests.unit.test_agent_tools import database, mutate, prepare, save, slide_content
from tests.unit.test_slide_ui_chat_tools import _slide_ui
from utils.asset_directory_utils import normalize_slide_asset_url


@pytest.mark.parametrize("native_result", [
    {"ok": True, "saved": False},
    {"ok": True, "result": {"saved": True}},
    {"unexpected": "result"},
    {"saved": "true"},
    {"saved": 1},
    {},
])
def test_native_unknown_or_wrapped_failure_rolls_back_before_receipt(
    tmp_path, monkeypatch, native_result
):
    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await prepare(client)
            await save(client, doc, epoch, slide_content())
            before = await client.call("read", doc)
            original = ChatTools.execute_validated
            calls = []

            async def unexpected(self, name, arguments):
                calls.append(name)
                await original(self, name, arguments)
                return copy.deepcopy(native_result)

            monkeypatch.setattr(ChatTools, "execute_validated", unexpected)
            operation = uuid.uuid4()
            args = {"index": 0, "layoutId": "intro",
                    "content": json.dumps(slide_content("must roll back")),
                    "replaceOldSlideAtIndex": True}
            result = await mutate(client, doc, epoch, "saveSlide", args,
                                  revision=before["revision"], operation=operation)
            assert result["status"] == "rejected"
            assert result["revision"] == before["revision"]
            after = await client.call("read", doc)
            assert after == before
            replay = await mutate(client, doc, epoch, "saveSlide", args,
                                  revision=before["revision"], operation=operation)
            assert replay["replayed"] and replay["status"] == "rejected"
            assert calls == ["saveSlide"]
            assert (await client.call("receipt", operation))["status"] == "rejected"

    asyncio.run(run())


def test_invalid_native_arguments_never_enter_repair_or_handler(tmp_path, monkeypatch):
    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await prepare(client)
            await save(client, doc, epoch, slide_content())
            before = await client.call("read", doc)

            def forbidden(*args, **kwargs):
                raise AssertionError("external input must not run argument repair")

            monkeypatch.setattr(ChatTools, "_repair_tool_args", forbidden)
            operation = uuid.uuid4()
            result = await mutate(client, doc, epoch, "updateElement",
                                  {"index": 0}, operation=operation)
            assert result["status"] == "rejected"
            assert result["result"]["code"] == "invalid_tool_arguments"
            assert await client.call("read", doc) == before
            assert (await client.call("receipt", operation))["status"] == "rejected"

    asyncio.run(run())


def test_original_png_survives_a_different_layout_and_local_edit(tmp_path, monkeypatch):
    import services.chat.memory_layer as native

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))

    def forbidden(*args, **kwargs):
        raise AssertionError("existing images must not initialize generation")

    monkeypatch.setattr(native, "ImageGenerationService", forbidden)

    async def run():
        async with database(tmp_path) as (sessions, clients):
            client = clients[0]
            image_path = tmp_path / "images" / "users" / str(client.owner) / "original.png"
            image_path.parent.mkdir(parents=True)
            Image.new("RGB", (32, 24), (17, 83, 151)).save(image_path)
            original_digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
            url = normalize_slide_asset_url(str(image_path))
            placeholder_paths = [image_path.with_name("intro-placeholder.png"),
                                 image_path.with_name("alternate-placeholder.png")]
            for path, color in zip(placeholder_paths, [(190, 40, 30), (40, 170, 60)]):
                Image.new("RGB", (32, 24), color).save(path)
            placeholder_urls = [normalize_slide_asset_url(str(path)) for path in placeholder_paths]
            assert len({url, *placeholder_urls}) == 3
            first = _slide_ui()
            first["components"].append({
                "id": "photo", "description": "Owned photo",
                "position": {"x": 300, "y": 40},
                "elements": [{"type": "image", "name": "Photo", "decorative": False,
                              "is_icon": False, "position": {"x": 0, "y": 0},
                              "size": {"width": 100, "height": 75}, "data": placeholder_urls[0]}],
            })
            alternate = copy.deepcopy(first)
            alternate["id"] = "alternate-photo"
            alternate["components"][2]["position"] = {"x": 600, "y": 80}
            alternate["components"][2]["elements"][0]["data"] = placeholder_urls[1]
            async with sessions() as session:
                # Defaults are valid owned assets too: a missed binding must fail
                # the content assertion rather than an unrelated permission check.
                session.add_all([ImageAsset(owner_id=client.owner, path=str(path), is_uploaded=True)
                                 for path in [image_path, *placeholder_paths]])
                template = await session.get(TemplateV2, "test-template")
                template.layouts = {"layouts": [first, alternate]}
                await session.commit()
            doc, epoch = await prepare(client)
            content = {**slide_content(), "photo": {"Photo": {
                "image_prompt": "generate an unrelated image", "image_url": url,
            }}}
            assert (await save(client, doc, epoch, content))["status"] == "applied"
            initial = await client.call("read", doc)
            assert initial["slides"][0]["ui"]["components"][2]["elements"][0]["data"] == url
            result = await mutate(client, doc, epoch, "saveSlide", {
                "index": 0, "layoutId": "alternate-photo", "content": json.dumps(content),
                "replaceOldSlideAtIndex": True,
            })
            assert result["status"] == "applied"
            swapped = await client.call("read", doc)
            photo = copy.deepcopy(swapped["slides"][0]["ui"]["components"][2])
            assert photo["position"] == {"x": 600, "y": 80}
            assert photo["elements"][0]["data"] == url
            assert (await mutate(client, doc, epoch, "updateElement", {
                "index": 0, "elementPath": "components[0].elements[0]", "text": "局部改标题",
            }))["status"] == "applied"
            final = await client.call("read", doc)
            assert final["slides"][0]["slideId"] == initial["slides"][0]["slideId"]
            assert final["slides"][0]["speakerNote"] == initial["slides"][0]["speakerNote"]
            assert final["slides"][0]["ui"]["components"][2] == photo
            assert hashlib.sha256(image_path.read_bytes()).hexdigest() == original_digest
            with Image.open(image_path) as actual:
                assert actual.size == (32, 24) and actual.getpixel((0, 0)) == (17, 83, 151)

    asyncio.run(run())
