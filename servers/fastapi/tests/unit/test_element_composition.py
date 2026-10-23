"""Authoring contract, real font checks and isolated durable workflow acceptance."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from templates.v2.composition import CompositionError
from templates.v2.element_composition import compose_elements, element_schema
from templates.v2.models.layouts import SlideLayout


ROOT = Path(__file__).resolve().parents[4]
FONTS = ROOT / "templates/graphite-technical/static"


@pytest.fixture
def layout():
    pack = json.loads((ROOT / "templates/graphite-technical/element-pack.json").read_text("utf-8"))
    return dict(id="graphite-elements", description="Element authoring", renderer="native-elements-v1", element_pack=pack, components=[])


def page():
    return dict(title="metadata only", root=dict(id="page", type="group", position=dict(x=0, y=0),
        size=dict(width=1280, height=720), children=[dict(id="heading", type="text", position=dict(x=88, y=60),
            size=dict(width=900, height=70), styleRef="page_title", content=dict(text="一个可编辑标题"))]))


def compile_page(content, layout):
    return compose_elements(content, layout, font_root=FONTS)


def test_native_tree_keeps_geometry_order_theme_and_no_duplicate_chrome(layout):
    content = page()
    content["root"]["children"][0]["props"] = {"font": {"size": 40, "color": "#EA580C"}}
    ui = compile_page(content, layout)
    SlideLayout.model_validate(ui)
    title = ui["components"][0]["elements"][0]["children"][0]
    assert title["position"] == dict(x=88, y=60)
    assert title["font"]["family"] == "Noto Sans SC"
    assert title["font"]["size"] == 40
    assert title["runs"] == [dict(text="一个可编辑标题")]
    assert "metadata only" not in str(ui)
    content["root"]["children"][0]["position"] = dict(x=360, y=240)
    assert compile_page(content, layout)["components"][0]["elements"][0]["children"][0]["position"] == dict(x=360, y=240)


def test_pinned_catalog_and_template_import_keep_pack(layout):
    from templates.default_templates import _coerce_slide_layouts
    from services.agent_tools.workflow_templates import layouts, schema_for
    schema = element_schema(layout)
    assert schema["x-designCatalog"]["canvas"] == dict(width=1280, height=720)
    assert len(schema["x-designCatalog"]["sourceHash"]) == 64
    imported = _coerce_slide_layouts(dict(layouts=[layout]))
    assert imported["layouts"][0]["element_pack"] == layout["element_pack"]
    assert "root" in schema_for(layouts(imported)[layout["id"]])["properties"]


@pytest.mark.parametrize("mutation,code", [
    (lambda p: p["root"].update(position=dict(x=1, y=0)), "element_root_canvas"),
    (lambda p: p["root"]["children"][0].update(position=dict(x=1200, y=60)), "element_outside_canvas"),
    (lambda p: p["root"]["children"][0].update(size=dict(width=100, height=20)), "element_text_overflow"),
    (lambda p: p["root"]["children"][0].update(styleRef="missing"), "element_schema_invalid"),
    (lambda p: p["root"]["children"][0].update(props={"font": {"size": 12}}), "element_font_size"),
    (lambda p: p["root"]["children"][0].update(content={"text": "内容", "html": "<script>bad()</script>"}), "element_schema_invalid"),
    (lambda p: p["root"]["children"].append(deepcopy(p["root"]["children"][0])), "element_tree_invalid"),
    (lambda p: p["root"]["children"][0].update(type="image", content={"src": "https://invalid/image.png"}), "element_schema_invalid"),
])
def test_invalid_content_is_repairable_and_never_silently_reflowed(layout, mutation, code):
    content = page()
    mutation(content)
    before = deepcopy(content)
    with pytest.raises(CompositionError, match=code):
        compile_page(content, layout)
    assert content == before


def test_group_coordinates_and_instances_expand_without_fixed_layout(layout):
    content = page()
    content["root"]["children"].insert(0, dict(id="frame", type="instance", compositionRef="page_base"))
    ui = compile_page(content, layout)
    group = ui["components"][0]["elements"][0]
    assert group["children"][0]["children"][0]["name"] == "frame/base"
    assert group["children"][1]["name"] == "heading"


def test_workflow_validates_then_persists_element_tree_and_owner_boundary(tmp_path, layout, monkeypatch):
    from models.sql.template_v2 import TemplateV2
    from models.sql.ppt_workflow import PptWorkflowRef
    from services.agent_tools import workflow_schemas as inputs
    from services.agent_tools.workflow_worker import drive
    from tests.unit.test_agent_tools import database
    from tests.unit.test_ppt_workflow import WorkflowClient
    from templates.v2.composition import Composer
    original = Composer.__init__
    monkeypatch.setattr(Composer, "__init__", lambda self, font_root=None: original(self, font_root or FONTS))

    async def run():
        async with database(tmp_path) as (sessions, owners):
            async with sessions() as session:
                template = await session.get(TemplateV2, "test-template")
                template.layouts = dict(layouts=[layout])
                await session.commit()
            client = WorkflowClient(sessions, owners[0].owner)
            prepared = await client.call("prepare", inputs.Prepare(title="元素验收", confirmed=True, outline=["封面", "机制"]))
            started = await client.call("start", inputs.Start(preparationRef=prepared["preparationRef"], templateRef=prepared["templates"][0]["templateRef"], layoutPlan=["graphite-elements", "graphite-elements"]))
            # The accepted snapshot remains fixed even if the catalog changes later.
            async with sessions() as session:
                template = await session.get(TemplateV2, "test-template")
                template.layouts = dict(layouts=[])
                await session.commit()
            content = page()
            content["root"]["children"][0]["size"]["height"] = 10
            request = dict(taskRef=started["taskRef"], finish=False, pages=[dict(slotId=started["slots"][0]["slotId"], content=content)])
            rejected = await client.call("submit", inputs.Submit(**request))
            assert rejected["status"] == "needs_input"
            assert "element_text_overflow" in str(rejected)
            request["pages"][0]["content"] = page()
            queued = await client.call("submit", inputs.Submit(**request))
            assert queued["status"] == "queued", queued
            async with sessions() as session:
                ref = await session.get(PptWorkflowRef, started["taskRef"])
                task_id = ref.task_id
            await drive(sessions, client.owner, task_id, budget_seconds=10)
            partial = await client.call("task", inputs.Task(taskRef=started["taskRef"]))
            assert partial["status"] == "awaiting_content"
            assert len(partial["committed"]) == 1
            first_slide_id = partial["slots"][0]["slideId"]
            second = await client.call("submit", inputs.Submit(taskRef=started["taskRef"], finish=True,
                pages=[dict(slotId=started["slots"][1]["slotId"], content=page())]))
            assert second["status"] == "queued"
            await drive(sessions, client.owner, task_id, budget_seconds=10)
            saved = await client.call("read_for_edit", inputs.Read(documentId=started["documentId"]))
            assert len(saved["slides"]) == 2
            assert saved["slides"][0]["slideId"] == first_slide_id
            assert "heading" in str(saved)
            assert "metadata only" not in str(saved)
            other = WorkflowClient(sessions, owners[1].owner)
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                await other.call("read_for_edit", inputs.Read(documentId=started["documentId"]))
    asyncio.run(run())


def test_reports_independent_text_errors_together_without_mutating_page(layout):
    content = page()
    first = content["root"]["children"][0]
    first["size"]["height"] = 10
    second = deepcopy(first)
    second.update(id="second", position=dict(x=88, y=240))
    content["root"]["children"].append(second)
    before = deepcopy(content)
    with pytest.raises(CompositionError) as caught:
        compile_page(content, layout)
    report = caught.value.report()
    assert report["code"] == "element_text_overflow"
    assert [item["path"] for item in report["issues"]] == ["heading", "second"]
    assert report["truncated"] is False
    assert content == before
