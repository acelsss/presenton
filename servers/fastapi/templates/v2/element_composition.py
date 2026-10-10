"""Element-level authoring through a pinned three-layer pack and native UI.

No automatic page chrome, layout solver, HTML, model calls or asset downloads.
The existing workflow owns authorization, snapshots, revisions and persistence.
"""
from copy import deepcopy
import math

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from templates.pack.compiler import PackCompiler, source_hash
from templates.pack.models import Node
from templates.v2.composition import Composer, CompositionError, font


RENDERER = "native-elements-v1"
MAX_NODES = 180
MAX_ISSUES = 20


class ElementCompositionErrors(CompositionError):
    """Retain the first-error contract and include bounded independent findings."""
    def __init__(self, issues):
        first = issues[0]
        super().__init__(first.code, first.path, first.detail)
        self.issues = issues

    def report(self):
        return {**super().report(), "issues": [issue.report() for issue in self.issues[:MAX_ISSUES]],
                "truncated": len(self.issues) > MAX_ISSUES}


def is_element_composition(layout):
    return isinstance(layout, dict) and layout.get("renderer") == RENDERER


def element_schema(layout):
    compiler = PackCompiler(layout["element_pack"])
    definitions = deepcopy(Node.model_json_schema()["$defs"])
    node = definitions["Node"]
    node["properties"].pop("source", None)
    node["properties"]["children"]["maxItems"] = 80
    node["properties"]["type"]["enum"] = ["text", "vector", "group", "instance", "table"]
    node["properties"]["styleRef"] = {"enum": [s.id for s in compiler.pack.elementStyles]}
    node["properties"]["compositionRef"] = {"enum": list(compiler.compositions)}
    node["allOf"] = [{
        "if": {"properties": {"type": {"const": "text"}}},
        "then": {"required": ["position", "size", "styleRef", "content"],
                 "properties": {"content": {"type": "object", "additionalProperties": False,
                    "required": ["text"], "properties": {"text": {"type": "string", "maxLength": 4000}}}}},
    }]
    catalog = {
        "canvas": compiler.pack.canvas.model_dump(),
        "sourceHash": source_hash(layout["element_pack"]),
        "theme": compiler.theme.model_dump(),
        "elementStyles": [{"id": s.id, "type": s.type, "properties": compiler.style(s.id)}
                          for s in compiler.pack.elementStyles],
        "compositions": [{"id": c.id, "name": c.name} for c in compiler.pack.compositions],
    }
    return {"type": "object", "additionalProperties": False, "$defs": definitions,
            "required": ["title", "root"],
            "properties": {"title": {"type": "string", "maxLength": 240}, "root": {"$ref": "#/$defs/Node"}},
            "x-designCatalog": catalog,
            "description": (
                "Element composition: submit {title,root}, not semantic blocks. Root is a group at (0,0) "
                "with size 1280x720. Use stable unique ids; child positions are relative to parent. "
                "Use the x-designCatalog styleRef/compositionRef values. The title field is metadata, "
                "so draw the visible title exactly once in root. No automatic header/footer. "
                "Text has content:{text}, position, size and styleRef. Optional props.font overrides "
                "size/color/bold while retaining the theme. Vector uses local props.points, shape:'polygon', "
                "closed:true for rectangles; closed:false for connector lines. For export-safe directional "
                "arrows draw a small filled closed polygon triangle at the line endpoint; the current "
                "export renderer does not draw end_marker by itself. "
                "Group has children. Instance uses compositionRef. Draw diagrams for the actual mechanism. "
                "Keep body >=22px, auxiliary text >=18px, captions >=16px; widen/reflow before shortening. "
                "Reserve font size*line height*line count+8px height. Bounds and text fit are checked. "
                "Geometry checks do not prove factual accuracy or visual quality; review rendered pages."
            )}


def _check_nodes(root, compiler):
    nodes = list(compiler.nodes(root))
    if len(nodes) > MAX_NODES:
        raise CompositionError("element_node_limit", "root", f"Use at most {MAX_NODES} expanded nodes per page")
    for node in nodes:
        kind = node["type"]
        if kind not in {"text", "vector", "group", "table"}:
            raise CompositionError("element_type_unsupported", node["id"], "Use text/vector/group/instance/table")
        allowed = {"text"} if kind == "text" else set()
        if set(node.get("content", {})) - allowed or node.get("source"):
            raise CompositionError("element_unused_content", node["id"], "Only text.content.text is display content; use native props for tables/vectors")
        if kind == "text" and not node.get("size"):
            raise CompositionError("element_text_size_required", node["id"], "Provide width and height for every text box")


def _check_text(root, compiler, font_root=None, *, collect=False):
    measurer = Composer(font_root)
    issues = []
    for node in compiler.nodes(root):
        if node["type"] != "text":
            continue
        try:
            native = compiler.native(node)
            settings = native.get("font", {})
            size = settings.get("size", 22)
            if not math.isfinite(size) or not 16 <= size <= 96:
                raise CompositionError("element_font_size", node["id"], "Font size must be 16..96px; reflow instead of shrinking text")
            line_height = settings.get("line_height", 1.35)
            if not math.isfinite(line_height) or not 1 <= line_height <= 2.2:
                raise CompositionError("element_line_height", node["id"], "Use line_height between 1 and 2.2")
            family = settings.get("family", "Noto Sans SC")
            filename = {"Noto Sans SC": "NotoSansSC-Regular.ttf", "JetBrains Mono": "JetBrainsMono-Regular.ttf"}.get(family)
            if not filename:
                raise CompositionError("element_font_unavailable", node["id"], "Use a bundled theme font")
            face = font(str(measurer.font_root / filename), round(size))
            width = node["size"]["width"]
            lines = 0
            for paragraph in node["content"].get("text", "").split("\n"):
                lines += 1
                line = ""
                for char in paragraph:
                    if face.getlength(char) > width + .5:
                        raise CompositionError("element_text_width", node["id"], "Increase width to fit the text")
                    if line and face.getlength(line + char) > width - 2:
                        lines += 1
                        line = ""
                    line += char
            needed = math.ceil(size * line_height * lines) + 4
            if needed > node["size"]["height"] + .5:
                raise CompositionError("element_text_overflow", node["id"],
                                       f"Needs {needed}px height, available {node['size']['height']:g}px; enlarge, reflow or refine wording without losing facts")
        except CompositionError as exc:
            if not collect:
                raise
            issues.append(exc)
    return issues



def compose_elements(content, layout, *, font_root=None):
    content = {key: value for key, value in content.items() if not key.startswith("__")}
    pending = [(content.get("root"), 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > 12 or count > MAX_NODES:
            raise CompositionError("element_node_limit", "root", "Use at most 180 nodes and 12 group levels")
        if isinstance(node, dict) and isinstance(node.get("children"), list):
            pending.extend((child, depth + 1) for child in node["children"])
    schema_error = next(Draft202012Validator(element_schema(layout)).iter_errors(content), None)
    if schema_error:
        raise CompositionError("element_schema_invalid", ".".join(map(str, schema_error.absolute_path)), "Use the returned element tree schema")
    try:
        compiler = PackCompiler(layout["element_pack"])
        request = Node.model_validate(content["root"])
        if request.type != "group" or request.position.x != 0 or request.position.y != 0 or not request.size or request.size.width != 1280 or request.size.height != 720:
            raise CompositionError("element_root_canvas", "root", "Root must be a group at (0,0), size 1280x720")
        expanded = compiler.expand(request)
        _check_nodes(expanded, compiler)
        outside = compiler.geometry_findings(expanded)
        issues = [CompositionError("element_outside_canvas", item["nodeId"],
                   "Move/resize this element inside the 1280x720 canvas") for item in outside]
        issues.extend(_check_text(expanded, compiler, font_root, collect=True))
        if issues:
            raise ElementCompositionErrors(issues)
        result = compiler.compile(mode="composition", root=content["root"])
        result["ui"]["id"] = layout["id"]
        return result["ui"]
    except CompositionError:
        raise
    except (ValueError, KeyError, TypeError, ValidationError, RecursionError) as exc:
        raise CompositionError("element_tree_invalid", "root", "Invalid style reference, node, native properties or geometry; use the pinned design catalog") from exc
