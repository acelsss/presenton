"""Native geometry/content regressions; real bundled fonts, isolated SQLite."""
import asyncio
import copy
import os
import re
from pathlib import Path

import pytest

from templates.v2.composition import Composer, CompositionError, compose
from templates.v2.composition_schema import BLOCKS, composition_schema
from templates.v2.models.layouts import SlideLayout, SlideLayouts


@pytest.fixture(autouse=True)
def bundled_fonts(monkeypatch):
    root = Path(os.environ.get("COMPOSITION_TEST_FONTS", "templates/graphite-technical/static"))
    if not (root / "NotoSansSC-Regular.ttf").is_file():
        pytest.skip("Set COMPOSITION_TEST_FONTS to the bundled graphite static directory")
    original = Composer.__init__
    monkeypatch.setattr(Composer, "__init__", lambda self, font_root=None: original(self, font_root or root))


def block(ref="E02", key="a", **fields):
    return dict(id=key, blockRef=ref, title="测试内容", **fields)


def page(node):
    return dict(title="真实中文测量", composition=node)


def elements(ui):
    return [e for c in ui["components"] for e in c["elements"]]


def test_real_native_model_and_stable_block_ids():
    content = page(dict(layout="row", weights=[3, 2], children=[block(body="左侧论点"), block(key="b", body="右侧依据")]))
    first = compose(content)
    SlideLayout.model_validate(first)
    content["composition"]["children"][1]["body"] = "修改右侧依据"
    second = compose(content)
    assert first["components"][1] == second["components"][1]
    assert second["components"][2]["id"] == "block-b"


def test_groups_use_distinct_geometry():
    positions = []
    for layout in ("row", "column", "grid"):
        ui = compose(page(dict(layout=layout, children=[block(key=str(i), body="内容") for i in range(3)])))
        positions.append([next(e["position"] for e in c["elements"] if e["type"] == "text") for c in ui["components"][1:]])
    assert positions[0][0]["y"] == positions[0][1]["y"]
    assert positions[1][0]["x"] == positions[1][1]["x"]
    assert positions[2][0]["y"] < positions[2][2]["y"]
    assert positions[0] != positions[2]


def test_confirmed_six_layers_cannot_be_padded_to_eight():
    content = page(block("A02", items=[dict(label=f"层{i}", detail="本层职责") for i in range(6)]))
    ui = compose(content, outline="六层技术栈分层图")
    assert len([e for e in elements(ui) if e.get("name", "").startswith("a-label-")]) == 6
    content["composition"]["items"] += [dict(label="额外层")] * 2
    with pytest.raises(CompositionError, match="composition_outline_count_mismatch"):
        compose(content, outline="六层技术栈分层图")


def test_long_chinese_overflow_is_repairable_not_shrunk():
    with pytest.raises(CompositionError, match="composition_height_insufficient"):
        compose(page(dict(layout="row", children=[block(body="需要完整保留的中文长文本" * 90), block(key="b", body="结论")])) )


def test_network_references_and_arrowheads_are_native():
    node = block("A03", items=[dict(id="x", label="输入"), dict(id="y", label="输出")], edges=[dict(from_="x")])
    node["edges"] = [{"from": "x", "to": "y", "label": "关系"}]
    ui = compose(page(node))
    SlideLayout.model_validate(ui)
    assert any(e.get("name") == "a-edge-0-arrow" for e in elements(ui))
    node["edges"][0]["to"] = "missing"
    with pytest.raises(CompositionError, match="composition_edge_reference"):
        compose(page(node))


def test_independent_bars_keep_exact_values_and_scale():
    ui = compose(page(block("D01", unit="%", scaleMax=100, independent=True, items=[dict(label="甲", value=72), dict(label="乙", value=68), dict(label="丙", value=91)])))
    es = elements(ui)
    assert [e["text"] for e in es if e.get("name", "").startswith("a-value-")] == ["72 %", "68 %", "91 %"]
    bars = [e for e in es if e.get("name", "").startswith("a-bar-")]
    assert (bars[0]["points"][1]["x"] - bars[0]["points"][0]["x"]) / 1112 == pytest.approx(.72)


def test_multiple_relations_do_not_share_the_same_node_port():
    node = block("A03", items=[dict(id=k, label=k) for k in ["a", "b", "c"]],
                 edges=[{"from": "a", "to": "b", "label": "第一种关系"}, {"from": "a", "to": "c", "label": "第二种关系"}])
    ui = compose(page(node))
    lines = [e for e in elements(ui) if e.get("name") in {"a-edge-0", "a-edge-1"}]
    assert lines[0]["points"][0]["x"] != lines[1]["points"][0]["x"]


def test_relation_labels_do_not_intersect_other_edges():
    node = block("A03", items=[dict(id=k, label=k) for k in ["a", "b", "c", "d"]],
                 edges=[{"from": a, "to": b, "label": "这是需要完整显示的关系说明文字"}
                        for a, b in [("a", "b"), ("b", "c"), ("d", "a"), ("d", "c")]])
    es = elements(compose(page(node)))
    labels = [e for e in es if "-edge-label-" in e.get("name", "")]
    lines = [e for e in es if re.fullmatch(r"a-edge-\d+", e.get("name", ""))]
    for label in labels:
        x, y = label["position"].values()
        right, bottom = x + label["size"]["width"], y + label["size"]["height"]
        for line in lines:
            for a, b in zip(line["points"], line["points"][1:]):
                if a["x"] == b["x"]:
                    assert not (x < a["x"] < right and max(a["y"], b["y"]) > y and min(a["y"], b["y"]) < bottom)
                else:
                    assert not (y < a["y"] < bottom and max(a["x"], b["x"]) > x and min(a["x"], b["x"]) < right)


@pytest.mark.parametrize("node,code", [
    (block(items=[dict(label="不能丢掉")]), "composition_unused_field"),
    (block("D01", unit="%", scaleMax=100, items=[dict(label="数据", value=1, detail="不能丢掉")]), "composition_unused_field"),
    (block("X02", body='{"unfinished":'), "composition_invalid_json"),
    (block("T01", columns=["列1", "列2"], rows=[["不完整"]]), "composition_table_width"),
])
def test_never_silently_drop_unsupported_or_incomplete_content(node, code):
    with pytest.raises(CompositionError, match=code):
        compose(page(node))


def test_code_characters_survive_wrapping():
    body = 'const result = input.map(value => value + 1);\n  return result;'
    ui = compose(page(block("X01", body=body)))
    text = next(e["text"] for e in elements(ui) if e.get("name") == "a-body")
    assert text.replace("\n", "") == body.replace("\n", "")


def test_chinese_code_uses_the_same_explicit_font_for_measurement_and_rendering():
    from PIL import ImageFont
    body = 'const message = "中文注释应完整显示，不能按缺失字形的宽度测量";'
    c = Composer()
    es = []
    c.text(es, "code", body, 0, 0, 220, mono=True)
    result = es[0]
    assert ''.join(run["text"] for run in result["runs"]) == result["text"]
    assert result["text"].replace('\n', '') == body
    assert {run["font"]["family"] for run in result["runs"]} == {"Noto Sans SC", "JetBrains Mono"}
    for run in result["runs"]:
        if not run["text"].isascii():
            assert run["font"]["family"] == "Noto Sans SC"
    fonts = {name: ImageFont.truetype(str(c.font_root / file), 19) for name, file in
             [("Noto Sans SC", "NotoSansSC-Regular.ttf"), ("JetBrains Mono", "JetBrainsMono-Regular.ttf")]}
    for line in result["text"].split('\n'):
        assert sum(fonts[family].getlength(part) for part, family in c.font_runs(line, True)) <= 218


def test_renderer_survives_template_import_and_old_layout_schema():
    from templates.default_templates import _coerce_slide_layouts
    from services.agent_tools.workflow_templates import layouts, schema_for
    native = compose(page(block(body="占位")))
    native["renderer"] = "native-blocks-v1"
    payload = _coerce_slide_layouts(dict(layouts=[native]))
    assert payload["layouts"][0]["renderer"] == "native-blocks-v1"
    assert "composition" in schema_for(layouts(payload)["graphite-compose"])["properties"]
    del native["renderer"]
    old = schema_for(layouts(dict(layouts=[native]))["graphite-compose"])
    assert "composition" not in old["properties"]


def test_workflow_rejects_mismatch_then_saves_native_composition(tmp_path):
    from models.sql.template_v2 import TemplateV2
    from services.agent_tools import workflow_schemas as inputs
    from tests.unit.test_agent_tools import database
    from tests.unit.test_ppt_workflow import WorkflowClient
    from services.agent_tools.workflow_worker import drive
    from models.sql.ppt_workflow import PptWorkflowRef

    async def run():
        async with database(tmp_path) as (sessions, owners):
            native = compose(page(block(body="中性预览")))
            native["renderer"] = "native-blocks-v1"
            async with sessions() as session:
                template = await session.get(TemplateV2, "test-template")
                template.layouts = dict(layouts=[native])
                await session.commit()
            client = WorkflowClient(sessions, owners[0].owner)
            prepared = await client.call("prepare", inputs.Prepare(title="六层检查", confirmed=True, outline=["六层技术栈分层图"]))
            started = await client.call("start", inputs.Start(preparationRef=prepared["preparationRef"], templateRef=prepared["templates"][0]["templateRef"], layoutPlan=["graphite-compose"]))
            content = page(block("A02", items=[dict(label=f"层{i}", detail="职责") for i in range(8)]))
            request = dict(editRef=started["editRef"], pages=[dict(slotId=started["slots"][0]["slotId"], content=content, speakerNote="审核说明")])
            rejected = await client.call("submit", inputs.Submit(**request))
            assert rejected["status"] == "needs_input", rejected
            assert "composition_outline_count_mismatch" in str(rejected)
            content["composition"]["items"] = content["composition"]["items"][:6]
            request["editRef"] = rejected["repairRef"]
            queued = await client.call("submit", inputs.Submit(**request))
            assert queued["status"] == "queued", queued
            async with sessions() as session:
                ref = await session.get(PptWorkflowRef, started["taskRef"])
                task_id = ref.task_id
            await drive(sessions, client.owner, task_id, budget_seconds=10)
            result = await client.call("task", inputs.Task(taskRef=started["taskRef"]))
            assert result["status"] == "completed", result
            saved = await client.call("read_for_edit", inputs.Read(documentId=started["documentId"]))
            assert len(saved["slides"]) == 1
            assert "block-a" in str(saved)
    asyncio.run(run())
