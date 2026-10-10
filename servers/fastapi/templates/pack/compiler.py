"""Deterministic parsing. No LLM, eval, HTML or implicit mode fallback."""
from copy import deepcopy
import hashlib
import json
import math

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as ContentValidationError

from templates.pack.models import Node, TemplatePack
from templates.v2.models.layouts import SlideLayout
from templates.v2.models.elements import Text, Vector, Image, Table, Group


COMPILER_VERSION = "template-pack-native-1"
STYLE_KEYS = {"font", "fill", "stroke", "shadow", "alignment", "border_radius", "corner_radii", "opacity"}
NATIVE_MODELS = {"text": Text, "vector": Vector, "image": Image, "table": Table, "group": Group}


def source_hash(pack: dict) -> str:
    return hashlib.sha256(json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _index(items):
    result = {}
    for item in items:
        if item.id in result:
            raise ValueError(f"Duplicate ID: {item.id}")
        result[item.id] = item
    return result


class PackCompiler:
    def __init__(self, source: dict, theme_ref: str | None = None, asset_urls: dict | None = None):
        self.pack = TemplatePack.model_validate(source)
        self.styles = _index(self.pack.elementStyles)
        self.compositions = _index(self.pack.compositions)
        self.layouts = _index(self.pack.layouts)
        self.patterns = _index(self.pack.pagePatterns)
        self.assets = _index(self.pack.assets)
        themes = _index(self.pack.themes)
        self.theme = themes[theme_ref or self.pack.defaultThemeRef]
        self.asset_urls = asset_urls or {key: f"asset:{key}" for key in self.assets}
        self._style_cache = {}

    def resolve(self, value):
        if isinstance(value, str) and value.startswith("$"):
            parts = value[1:].split(".")
            if len(parts) != 2:
                raise ValueError(f"Invalid token: {value}")
            resolved = self.theme.tokens[parts[0]][parts[1]]
            if isinstance(resolved, str) and resolved.startswith("$"):
                raise ValueError(f"Tokens must contain literals: {value}")
            return deepcopy(resolved)
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Non-finite numeric value")
        return value

    def style(self, ref, stack=()):
        if not ref:
            return {}
        if ref in stack:
            raise ValueError(f"Style inheritance cycle: {ref}")
        if ref in self._style_cache:
            return deepcopy(self._style_cache[ref])
        recipe = self.styles[ref]
        if set(recipe.properties) - STYLE_KEYS:
            raise ValueError(f"Unsupported style properties: {ref}")
        base = {}
        if recipe.extends:
            if self.styles[recipe.extends].type != recipe.type:
                raise ValueError(f"Style inheritance type mismatch: {ref}")
            base = self.style(recipe.extends, (*stack, ref))
        for key, value in self.resolve(recipe.properties).items():
            base[key] = {**base[key], **value} if isinstance(value, dict) and isinstance(base.get(key), dict) else value
        self._style_cache[ref] = base
        return deepcopy(base)

    def expand(self, node: Node, stack=(), prefix="", seen=None):
        seen = seen if seen is not None else set()
        result = node.model_dump(mode="json", exclude_none=True)
        result["id"] = prefix + node.id
        if result["id"] in seen:
            raise ValueError(f"Duplicate node ID: {result['id']}")
        seen.add(result["id"])
        if len(seen) > 2000 or len(stack) > 32:
            raise ValueError("Node expansion limit exceeded")
        if node.type == "instance":
            ref = node.compositionRef
            if ref in stack:
                raise ValueError(f"Composition dependency cycle: {ref}")
            result["type"] = "group"
            result["children"] = [self.expand(self.compositions[ref].root, (*stack, ref), result["id"] + "/", seen)]
        else:
            if node.compositionRef:
                raise ValueError("compositionRef requires an instance node")
            if node.type != "group" and node.children:
                raise ValueError(f"Leaf node has children: {node.id}")
            result["children"] = [self.expand(child, stack, prefix, seen) for child in node.children]
        return result

    def nodes(self, root):
        yield root
        for child in root.get("children", []):
            yield from self.nodes(child)

    def geometry_findings(self, root):
        findings = []
        def walk(node, offset=(0, 0)):
            x = offset[0] + node["position"]["x"]
            y = offset[1] + node["position"]["y"]
            bounds = node.get("size")
            if node["type"] == "vector":
                points = node["props"].get("points", [])
                if points:
                    xs, ys = [p["x"] + x for p in points], [p["y"] + y for p in points]
                    bx, by, right, bottom = min(xs), min(ys), max(xs), max(ys)
                else:
                    bx, by, right, bottom = x, y, x, y
            else:
                bx, by = x, y
                right, bottom = x + (bounds or {}).get("width", 0), y + (bounds or {}).get("height", 0)
            if not all(math.isfinite(value) for value in (bx, by, right, bottom)):
                raise ValueError(f"Non-finite geometry: {node['id']}")
            if bx < -.1 or by < -.1 or right > self.pack.canvas.width + .1 or bottom > self.pack.canvas.height + .1:
                findings.append({"severity": "warning", "code": "outside_canvas", "nodeId": node["id"], "message": "Element bounds extend outside the slide; inspect clipping."})
            for child in node.get("children", []):
                walk(child, (x, y))
        walk(root)
        return findings

    def content_schema(self, layout_ref):
        layout = self.layouts[layout_ref]
        nodes = {node["id"]: node for node in self.nodes(self.expand(layout.root))}
        properties, required = {}, []
        for key, binding in layout.bindings.items():
            node = nodes[binding.nodeId]
            expected = {"content.text": "text", "content.src": "image", "props.rows": "table"}[binding.property]
            if node["type"] != expected or binding.valueType != ("array" if expected == "table" else "string"):
                raise ValueError(f"Binding type mismatch: {key}")
            example = node[binding.property.split(".")[0]].get(binding.property.split(".")[1])
            properties[key] = {"type": binding.valueType, "default": example}
            if binding.valueType == "array":
                properties[key]["items"] = {"type": "array", "items": {"type": "object"}}
            if binding.required:
                required.append(key)
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}

    def native(self, node):
        if node.get("styleRef") and self.styles[node["styleRef"]].type != node["type"]:
            raise ValueError(f"Style type mismatch: {node['id']}")
        props = self.resolve(node.get("props", {}))
        allowed = NATIVE_MODELS[node["type"]].model_fields
        if set(props) - set(allowed):
            raise ValueError(f"Unsupported native properties: {node['id']}: {set(props) - set(allowed)}")
        if any(key in props for key in ("type", "children", "child", "position", "size", "id")):
            raise ValueError(f"Reserved native properties: {node['id']}")
        native = self.style(node.get("styleRef"))
        for key, value in props.items():
            native[key] = {**native[key], **value} if isinstance(value, dict) and isinstance(native.get(key), dict) else value
        native["type"] = node["type"]
        if set(native) - set(allowed):
            raise ValueError(f"Style has properties unsupported by {node['type']}: {node['id']}")
        native["position"] = node["position"]
        if node.get("size"):
            native["size"] = node["size"]
        if node["type"] == "group":
            native["name"] = node["id"]
            native["children"] = [self.native(child) for child in node["children"]]
        elif node["type"] == "vector":
            native["points"] = [{"x": point["x"] + node["position"]["x"], "y": point["y"] + node["position"]["y"]} for point in native["points"]]
            native.pop("position", None)
            native.pop("size", None)
        elif node["type"] == "text":
            text = node["content"].get("text", "")
            runs = native.get("runs", [])
            if "".join(run.get("text", "") for run in runs) != text:
                native["runs"] = [{"text": text}]
            native.update(name=node["id"], decorative=False, min_length=0, max_length=max(100, len(text) * 2))
        elif node["type"] == "image":
            asset_ref = node["content"].get("src", "")
            if not asset_ref.startswith("asset:") or asset_ref[6:] not in self.assets:
                raise ValueError(f"Image must refer to a pinned pack asset: {node['id']}")
            native.update(data=self.asset_urls[asset_ref[6:]], name=node["id"], decorative=True, is_icon=False)
        elif node["type"] == "table":
            native.update(name=node["id"], decorative=True, min_rows=0, max_rows=max(1, len(native.get("rows", []))), min_columns=0, max_columns=max(1, len(native.get("columns", []))))
        return native

    def compile(self, *, mode: str, ref: str | None = None, root: dict | None = None, content: dict | None = None):
        if abs(self.pack.canvas.width - 1280) > .01 or abs(self.pack.canvas.height - 720) > .01:
            raise ValueError("Native pack preview currently requires a 1280 x 720 canvas")
        if mode not in self.pack.supportedModes:
            raise ValueError(f"Mode not supported: {mode}")
        if mode == "template":
            if root is not None:
                raise ValueError("Template mode does not accept a custom root")
            layout = self.layouts[ref]
            expanded = self.expand(layout.root)
            schema = self.content_schema(ref)
            values = content if content is not None else {key: prop["default"] for key, prop in schema["properties"].items()}
            Draft202012Validator(schema).validate(values)
            nodes = {node["id"]: node for node in self.nodes(expanded)}
            for key, value in values.items():
                binding = layout.bindings[key]
                section, field = binding.property.split(".")
                nodes[binding.nodeId][section][field] = value
        elif mode == "composition":
            if content:
                raise ValueError("Composition mode accepts a node tree, not layout bindings")
            expanded = self.expand(Node.model_validate(root) if root is not None else self.compositions[ref].root)
        else:
            raise ValueError(f"Unknown mode: {mode}")
        ui = {"id": ref or "composed-page", "description": self.pack.name, "components": [{"id": expanded["id"], "description": self.pack.name, "position": {"x": 0, "y": 0}, "elements": [self.native(expanded)]}]}
        # Validate against the production renderer/export contract. Keep IDs in
        # the pack and a separate mapping since native models omit node IDs.
        parsed = SlideLayout.model_validate(ui).model_dump(mode="json", exclude_none=True)
        paths = {}
        def walk(node, path):
            paths[node["id"]] = path
            for i, child in enumerate(node.get("children", [])):
                walk(child, f"{path}.children.{i}")
        walk(expanded, "components.0.elements.0")
        return {"ui": parsed, "resolvedRoot": expanded, "nodePaths": paths, "compilerVersion": COMPILER_VERSION, "sourceHash": source_hash(self.pack.model_dump(mode="json")), "findings": self.geometry_findings(expanded)}


def validate_pack(source: dict, asset_urls=None):
    findings = []
    counts = {}
    try:
        compiler = PackCompiler(source, asset_urls=asset_urls)
        for key in ("themes", "elementStyles", "compositions", "pagePatterns", "layouts", "assets"):
            counts[key] = len(getattr(compiler.pack, key))
        for theme in compiler.pack.themes:
            alternate = PackCompiler(source, theme.id, asset_urls)
            for style in compiler.pack.elementStyles:
                alternate.style(style.id)
            for layout in compiler.pack.layouts:
                compiled = alternate.compile(mode="template", ref=layout.id)
                findings.extend({**finding, "sourceSlide": layout.sourceSlide, "layoutId": layout.id} for finding in compiled["findings"])
            for component in compiler.pack.compositions:
                alternate.compile(mode="composition", ref=component.id)
        for pattern in compiler.pack.pagePatterns:
            if pattern.compositionRef not in compiler.compositions:
                raise ValueError(f"Missing page pattern composition: {pattern.id}")
        for layout in compiler.pack.layouts:
            for node in compiler.nodes(compiler.expand(layout.root)):
                for value in node["position"].values():
                    if not math.isfinite(value):
                        raise ValueError(f"Non-finite position: {node['id']}")
                if node["position"]["x"] < 0 or node["position"]["y"] < 0:
                    findings.append({"severity": "warning", "code": "negative_position", "nodeId": node["id"], "layoutId": layout.id})
        findings.extend(compiler.pack.diagnostics)
    except (ValueError, KeyError, TypeError, RecursionError, ContentValidationError) as exc:
        findings.append({"severity": "error", "code": "pack_invalid", "message": str(exc)[:2000]})
    return {"sourceHash": source_hash(source), "compilerVersion": COMPILER_VERSION, "counts": counts, "checks": {"structureAndCompile": "fail" if any(f.get("severity") == "error" for f in findings) else "pass", "visual": "unrun", "editSave": "unrun", "pptxExport": "unrun", "pdfExport": "unrun"}, "status": "draft", "findings": findings}
