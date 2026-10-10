"""Build the bundled element-authoring layout; preserve the block compatibility layout."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "servers/fastapi"))

from templates.pack.compiler import PackCompiler, source_hash


def graphite_pack():
    colors = {"paper": "#FFFFFF", "ink": "#111827", "surface": "#F3F4F6", "accent": "#EA580C", "muted": "#6B7280", "line": "#D1D5DB"}
    sizes = {"cover": 84, "title": 38, "heading": 26, "body": 22, "small": 18, "caption": 16}
    theme = {"id": "graphite", "name": "石墨", "tokens": {"colors": colors,
        "fonts": {"body": "Noto Sans SC", "code": "JetBrains Mono"}, "typography": sizes,
        "spacing": {"margin": 64, "gap": 24}, "radii": {"card": 8}}}
    styles = []
    for key, token in [("cover_title", "cover"), ("page_title", "title"), ("heading", "heading"),
                       ("body", "body"), ("small", "small"), ("caption", "caption")]:
        styles.append({"id": key, "type": "text", "name": key, "properties": {
            "font": {"family": "$fonts.body", "size": f"$typography.{token}",
                     "color": "$colors.ink", "line_height": 1.35 if token in {"body", "small", "caption"} else 1.2,
                     "bold": token in {"cover", "title", "heading"}},
            "alignment": {"horizontal": "left", "vertical": "top"}}})
    styles += [
        {"id": "muted", "type": "text", "name": "辅助说明", "extends": "small", "properties": {"font": {"color": "$colors.muted"}}},
        {"id": "accent_text", "type": "text", "name": "强调文字", "extends": "heading", "properties": {"font": {"color": "$colors.accent"}}},
        {"id": "white_text", "type": "text", "name": "深色背景文字", "extends": "body", "properties": {"font": {"color": "$colors.paper"}}},
    ]
    for key in ("paper", "surface", "ink", "accent"):
        styles.append({"id": key, "type": "vector", "name": key, "properties": {"fill": {"color": f"$colors.{key}"}}})
    styles.append({"id": "connector", "type": "vector", "name": "连线", "properties": {"stroke": {"color": "$colors.line", "width": 2}}})
    def rect(key, x, y, width, height, style):
        return {"id": key, "type": "vector", "position": {"x": x, "y": y}, "styleRef": style,
                "props": {"shape": "polygon", "closed": True, "points": [{"x": 0, "y": 0}, {"x": width, "y": 0}, {"x": width, "y": height}, {"x": 0, "y": height}]}}
    base = {"id": "base", "type": "group", "children": [rect("paper", 0, 0, 1280, 720, "paper"),
        {"id": "footer_rule", "type": "vector", "position": {"x": 64, "y": 668}, "styleRef": "connector",
         "props": {"closed": False, "points": [{"x": 0, "y": 0}, {"x": 1152, "y": 0}]}}]}
    return {"schemaVersion": "1.0", "id": "graphite-elements", "version": "1.1.0", "name": "石墨元素自由组合",
        "supportedModes": ["composition"], "canvas": {"width": 1280, "height": 720}, "defaultThemeRef": "graphite",
        "themes": [theme], "elementStyles": styles,
        "compositions": [{"id": "page_base", "name": "白底与页脚线，可选", "root": base}],
        "pagePatterns": [], "layouts": [], "assets": [],
        "provenance": {"kind": "first-party-design-system", "palette": "graphite-technical", "layoutPolicy": "LLM authors geometry; no fixed page slots"}, "diagnostics": []}


def build():
    directory = ROOT / "templates/graphite-technical"
    pack = graphite_pack()
    root = {"id": "preview", "type": "group", "position": {"x": 0, "y": 0}, "size": pack["canvas"], "children": [
        {"id": "base", "type": "instance", "compositionRef": "page_base"},
        {"id": "title", "type": "text", "position": {"x": 88, "y": 176}, "size": {"width": 1100, "height": 120},
         "styleRef": "cover_title", "content": {"text": "石墨 · 元素自由组合"}},
        {"id": "detail", "type": "text", "position": {"x": 88, "y": 350}, "size": {"width": 1050, "height": 80},
         "styleRef": "body", "content": {"text": "逐页设计文字、图形与连线，复用主题和元素样式。"}}]}
    ui = PackCompiler(pack).compile(mode="composition", root=root)["ui"]
    ui.update(id="graphite-elements", description="元素自由组合（优先）：LLM设计坐标、尺寸、文字、图形与连线，使用三层主题与样式。", renderer="native-elements-v1", element_pack=pack)
    target = directory / "template.json"
    template = json.loads(target.read_text("utf-8"))
    template["description"] = "石墨主题；元素树自由设计与内容块兼容布局。"
    layouts = template["layouts"]["layouts"]
    template["layouts"]["layouts"] = [ui, *[layout for layout in layouts if layout["id"] != ui["id"]]]
    target.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (directory / "element-pack.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"layoutId": ui["id"], "sourceHash": source_hash(pack), "styles": len(pack["elementStyles"])}))


if __name__ == "__main__":
    build()
