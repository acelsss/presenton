"""Measure constrained compositions and produce ordinary editable Template V2 UI.

No model, network, database, HTML, or executable template content. IDs map blocks
to native components. Typography never shrinks to hide overflow.
"""
import math
import re
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator
from PIL import ImageFont

from .composition_schema import BLOCKS, composition_schema

INK, PAPER, SURFACE, ACCENT = "#111827", "#FFFFFF", "#F3F4F6", "#EA580C"


class CompositionError(ValueError):
    def __init__(self, code, path, detail):
        self.code, self.path, self.detail = code, path, detail
        super().__init__(f"{code} at {path}: {detail}")

    def report(self):
        return {"code": self.code, "path": self.path, "detail": self.detail}


@lru_cache(maxsize=64)
def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError as exc:
        raise CompositionError("composition_font_unavailable", "font", "Install the template's bundled font") from exc


class Composer:
    def __init__(self, font_root=None):
        self.font_root = Path(font_root) if font_root else Path(__file__).resolve().parents[4] / "templates/graphite-technical/static"
        self.components = []
        self.ids = set()

    @staticmethod
    def font_runs(value, mono):
        parts = re.findall(r"[\x00-\x7f]+|[^\x00-\x7f]+", value) if mono else [value]
        return [(part, "JetBrains Mono" if mono and part.isascii() else "Noto Sans SC") for part in parts]

    def wrap(self, value, width, size, mono=False):
        if width < size * 2:
            raise CompositionError("composition_width_insufficient", "text", "Increase block width")
        faces = {family: font(str(self.font_root / filename), int(size)) for family, filename in
                 [("Noto Sans SC", "NotoSansSC-Regular.ttf"), ("JetBrains Mono", "JetBrainsMono-Regular.ttf")]}
        lines = []
        for paragraph in str(value).split("\n"):
            line = ""
            # Preserve all characters, including code whitespace and punctuation.
            for char in paragraph:
                length = sum(faces[family].getlength(part) for part, family in self.font_runs(line + char, mono))
                if line and length > width - 2:
                    lines.append(line)
                    line = ""
                line += char
            lines.append(line)
        return "\n".join(lines), len(lines) * math.ceil(size * 1.45)

    def height(self, value, width, size=19, mono=False):
        return self.wrap(value, width, size, mono)[1] if value else 0

    def text(self, elements, name, value, x, y, width, size=19, color=INK, bold=False, mono=False, max_height=None):
        if not value:
            return 0
        wrapped, height = self.wrap(value, width, size, mono)
        if max_height is not None and height > max_height + 0.5:
            raise CompositionError("composition_text_overflow", name,
                                   f"Needs {height}px height, available {round(max_height)}px; enlarge/reflow/split, preserve content")
        family = "JetBrains Mono" if mono else "Noto Sans SC"
        style = {"family": family, "size": size, "color": color, "bold": bold, "line_height": 1.45}
        elements.append({"type": "text", "name": name, "text": wrapped,
                         "decorative": False, "min_length": 0, "max_length": max(1, len(wrapped) * 2),
                         "position": {"x": round(x, 2), "y": round(y, 2)},
                         "size": {"width": round(width, 2), "height": height}, "font": style,
                         "runs": [{"text": part, "font": {**style, "family": family}}
                                  for part, family in self.font_runs(wrapped, mono)],
                         "alignment": {"horizontal": "left", "vertical": "top"}})
        return height

    @staticmethod
    def rect(elements, name, x, y, width, height, fill=SURFACE, radius=8):
        elements.append({"type": "vector", "name": name, "shape": "polygon", "closed": True,
                         "points": [{"x": x, "y": y}, {"x": x + width, "y": y},
                                    {"x": x + width, "y": y + height}, {"x": x, "y": y + height}],
                         "corner_radii": [radius] * 4, "fill": {"color": fill, "opacity": 1}})

    @staticmethod
    def line(elements, name, points, color=ACCENT, arrow=False):
        elements.append({"type": "vector", "name": name, "shape": "polygon", "closed": False,
                         "points": [{"x": x, "y": y} for x, y in points],
                         "stroke": {"color": color, "width": 2, "opacity": 1}})
        if arrow:
            (x0, y0), (x, y) = points[-2:]
            theta = math.atan2(y - y0, x - x0)
            wing = [(x - 9 * math.cos(theta - .5), y - 9 * math.sin(theta - .5)), (x, y),
                    (x - 9 * math.cos(theta + .5), y - 9 * math.sin(theta + .5))]
            Composer.line(elements, name + "-arrow", wing, color)

    def row_height(self, item, width, mode):
        if mode == "layers":
            return max(self.height(item["label"], width * .28, 20), self.height(item.get("detail", ""), width * .65, 17)) + 18
        return self.height(item["label"], width, 21) + self.height(item.get("detail", ""), width, 17) + 14

    def minimum(self, node, width):
        if "layout" in node:
            kids, gap = node["children"], node.get("gap", 24)
            if node["layout"] == "column":
                return sum(self.minimum(k, width) for k in kids) + gap * (len(kids) - 1)
            cols = len(kids) if node["layout"] == "row" else node.get("columns", 2)
            widths = self.widths(node, width, cols)
            rows = [kids[i:i + cols] for i in range(0, len(kids), cols)]
            return sum(max(self.minimum(k, widths[j]) for j, k in enumerate(row)) for row in rows) + gap * (len(rows) - 1)
        mode = BLOCKS[node["blockRef"]]
        inner = width - 40
        head = self.height(node["title"], inner, 24) + (12 if node["title"] else 0)
        body = self.height(node.get("body", ""), inner, 19, mode == "code")
        note = self.height(node.get("note", ""), inner, 14) + (12 if node.get("note") else 0)
        items = node.get("items", [])
        if mode in {"list", "timeline", "layers"}:
            content = sum(self.row_height(i, inner - (42 if mode in {"list", "timeline"} else 0), mode) for i in items)
        elif mode in {"flow", "network"}:
            if mode == "flow" and node.get("direction") == "column":
                content = sum(self.row_height(i, inner - 24, mode) + 20 for i in items) + 20 * (len(items) - 1)
            else:
                w = (inner - 30 * (len(items) - 1)) / max(1, len(items)) - 24
                content = max((self.row_height(i, w, mode) + 20 for i in items), default=0)
                content += self.edge_space(node, inner) if mode == "network" else 0
        elif mode == "bars":
            content = sum(self.height(i["label"], inner - 115, 17) + 30 for i in items)
        elif mode == "table":
            cols = len(node.get("columns", []))
            w = inner / max(1, cols) - 20
            content = sum(max(self.height(c, w, 17) for c in row) + 18 for row in [node.get("columns", [])] + node.get("rows", []) if row)
        elif mode == "metric":
            content = self.height(node.get("value", ""), inner, 60) + self.height(node.get("unit", ""), inner, 17)
        elif mode == "hero":
            head = self.height(node["title"], inner, 48) + 20
            content = 0
        else:
            content = 0
        return 40 + head + body + (16 if body else 0) + content + note

    @staticmethod
    def widths(node, width, count):
        gap = node.get("gap", 24)
        weights = node.get("weights", [1] * count) if node["layout"] == "row" else [1] * count
        if len(weights) != count:
            raise CompositionError("composition_weight_count", "weights", "One weight per child is required")
        usable = width - gap * (count - 1)
        return [usable * w / sum(weights) for w in weights]

    def place(self, node, x, y, width, height, path="composition"):
        minimum = self.minimum(node, width)
        if minimum > height + .5:
            raise CompositionError("composition_height_insufficient", path,
                                   f"Needs {math.ceil(minimum)}px, available {math.floor(height)}px; reflow, split or shorten without losing meaning")
        if "layout" not in node:
            self.block(node, x, y, width, height)
            return
        kids, gap = node["children"], node.get("gap", 24)
        if node["layout"] == "column":
            minima = [self.minimum(k, width) for k in kids]
            free = height - sum(minima) - gap * (len(kids) - 1)
            weights = node.get("weights", [1] * len(kids))
            if len(weights) != len(kids):
                raise CompositionError("composition_weight_count", path, "One weight per child is required")
            for i, (kid, small) in enumerate(zip(kids, minima)):
                h = small + free * weights[i] / sum(weights)
                self.place(kid, x, y, width, h, f"{path}.children[{i}]")
                y += h + gap
            return
        cols = len(kids) if node["layout"] == "row" else node.get("columns", 2)
        widths = self.widths(node, width, cols)
        rows = [kids[i:i + cols] for i in range(0, len(kids), cols)]
        minima = [max(self.minimum(k, widths[j]) for j, k in enumerate(row)) for row in rows]
        free = height - sum(minima) - gap * (len(rows) - 1)
        for row_index, row in enumerate(rows):
            h, left = minima[row_index] + free / len(rows), x
            for col, kid in enumerate(row):
                self.place(kid, left, y, widths[col], h, f"{path}.children[{row_index * cols + col}]")
                left += widths[col] + gap
            y += h + gap

    def block(self, node, x, y, width, height):
        key, mode = node["id"], BLOCKS[node["blockRef"]]
        if key in self.ids:
            raise CompositionError("composition_duplicate_id", key, "Use unique block IDs")
        self.ids.add(key)
        elements = []
        tone = node.get("tone", "plain")
        color = PAPER if tone == "ink" else INK
        if tone != "plain":
            self.rect(elements, key + "-surface", x, y, width, height, INK if tone == "ink" else SURFACE)
        if tone == "accent":
            self.rect(elements, key + "-accent", x, y, 4, height, ACCENT, 0)
        left, top, inner = x + 20, y + 20, width - 40
        if node.get("align") == "center":
            top += max(0, height - self.minimum(node, width)) / 2
        top += self.text(elements, key + "-title", node["title"], left, top, inner,
                         48 if mode == "hero" else 24, color, True) + (12 if node["title"] else 0)
        body = node.get("body", "")
        top += self.text(elements, key + "-body", body, left, top, inner, 19, color,
                         mono=mode == "code") + (16 if body else 0)
        items = node.get("items", [])
        if mode in {"list", "layers", "timeline"}:
            for i, item in enumerate(items):
                row_h = self.row_height(item, inner - (42 if mode in {"list", "timeline"} else 0), mode)
                if mode == "layers":
                    self.rect(elements, f"{key}-stripe-{i}", left, top + 3, 4, row_h - 12, ACCENT, 0)
                    self.text(elements, f"{key}-label-{i}", item["label"], left + 14, top, inner * .28, 20, color, True)
                    self.text(elements, f"{key}-detail-{i}", item.get("detail", ""), left + inner * .35, top + 2, inner * .65, 17, color)
                else:
                    inset = 42
                    if mode == "timeline":
                        if i < len(items) - 1:
                            self.line(elements, f"{key}-rail-{i}", [(left + 8, top + 16), (left + 8, top + row_h + 16)], ACCENT)
                        self.rect(elements, f"{key}-tick-{i}", left + 3, top + 11, 10, 10, ACCENT, 5)
                    elif node["blockRef"] == "L01":
                        self.text(elements, f"{key}-number-{i}", f"{i + 1:02d}", left, top, 36, 19, ACCENT, True)
                    h = self.text(elements, f"{key}-label-{i}", item["label"], left + inset, top, inner - inset, 21, color, True)
                    self.text(elements, f"{key}-detail-{i}", item.get("detail", ""), left + inset, top + h, inner - inset, 17, color)
                if mode != "timeline":
                    self.line(elements, f"{key}-rule-{i}", [(left, top + row_h - 7), (left + inner, top + row_h - 7)], SURFACE if tone != "surface" else PAPER)
                top += row_h
        elif mode in {"flow", "network"}:
            top = self.nodes(elements, node, left, top, inner, color)
        elif mode == "bars":
            scale = node["scaleMax"]
            for i, item in enumerate(items):
                value = item["value"]
                top += self.text(elements, f"{key}-label-{i}", item["label"], left, top, inner - 115, 17, color)
                self.text(elements, f"{key}-value-{i}", f"{value:g} {node['unit']}", left + inner - 110, top - 25, 110, 17, color, True)
                self.rect(elements, f"{key}-track-{i}", left, top + 5, inner, 10, SURFACE, 0)
                if value:
                    self.rect(elements, f"{key}-bar-{i}", left, top + 5, inner * value / scale, 10, ACCENT, 0)
                top += 30
        elif mode == "table":
            columns = node["columns"]
            cell_w = inner / len(columns)
            for i, row in enumerate([columns] + node["rows"]):
                row_h = max(self.height(cell, cell_w - 20, 17) for cell in row) + 18
                if i == 0:
                    self.rect(elements, key + "-header", left, top, inner, row_h, INK, 0)
                for j, cell in enumerate(row):
                    self.text(elements, f"{key}-cell-{i}-{j}", cell, left + cell_w * j + 10, top + 8,
                              cell_w - 20, 17, PAPER if i == 0 else color, i == 0)
                self.line(elements, f"{key}-row-{i}", [(left, top + row_h), (left + inner, top + row_h)], SURFACE)
                top += row_h
        elif mode == "metric":
            top += self.text(elements, key + "-value", node["value"], left, top, inner, 60, ACCENT, True)
            top += self.text(elements, key + "-unit", node.get("unit", ""), left, top, inner, 17, color)
        note = node.get("note", "")
        note_h = self.height(note, inner, 14)
        self.text(elements, key + "-note", note, left, min(top + 12, y + height - 20 - note_h), inner, 14, color)
        self.components.append({"id": "block-" + key, "description": node["blockRef"] + " · " + node["title"],
                                "position": {"x": 0, "y": 0}, "elements": elements})

    def nodes(self, elements, node, x, y, width, color):
        key, items = node["id"], node["items"]
        network = BLOCKS[node["blockRef"]] == "network"
        vertical = node.get("direction") == "column" and not network
        gap = 30
        w = width if vertical else (width - gap * (len(items) - 1)) / len(items)
        heights = [self.row_height(item, w - 24, "flow") + 20 for item in items]
        boxes = []
        top = y + (self.edge_space(node, width) if network else 0)
        for i, item in enumerate(items):
            left, h = (x if vertical else x + i * (w + gap)), heights[i] if vertical else max(heights)
            boxes.append((left, top, w, h))
            self.rect(elements, f"{key}-node-{i}", left, top, w, h, SURFACE)
            h1 = self.text(elements, f"{key}-node-label-{i}", item["label"], left + 12, top + 10, w - 24, 21, INK, True)
            self.text(elements, f"{key}-node-detail-{i}", item.get("detail", ""), left + 12, top + 10 + h1, w - 24, 17, INK)
            if vertical:
                top += h + 20
        if network:
            ids = {item["id"]: i for i, item in enumerate(items)}
            lane = y
            for i, (edge, route) in enumerate(zip(node["edges"], self.edge_routes(node, width))):
                a, b = boxes[ids[edge["from"]]], boxes[ids[edge["to"]]]
                sx, tx = x + route["source"], x + route["target"]
                lane += route["height"]
                self.line(elements, f"{key}-edge-{i}", [(sx, a[1]), (sx, lane), (tx, lane), (tx, b[1] - 3)], arrow=True)
                if edge.get("label"):
                    self.text(elements, f"{key}-edge-label-{i}", edge["label"], x + route["label_x"],
                              lane - route["height"] + 10, route["label_width"], 13, color)
        else:
            for i in range(len(boxes) - 1):
                a, b = boxes[i:i + 2]
                points = ([(a[0] + a[2] / 2, a[1] + a[3] + 2), (b[0] + b[2] / 2, b[1] - 3)] if vertical else
                          [(a[0] + a[2] + 3, a[1] + a[3] / 2), (b[0] - 3, b[1] + b[3] / 2)])
                self.line(elements, f"{key}-edge-{i}", points, arrow=True)
        return max(box[1] + box[3] for box in boxes)

    def edge_routes(self, node, width):
        step = (width + 30) / len(node["items"])
        boxes = [(i * step, 0, step - 30, 0) for i in range(len(node["items"]))]
        routes, ports = [], []
        for i, edge in enumerate(node["edges"]):
            sx = self.edge_port(node, edge["from"], i, boxes)
            tx = self.edge_port(node, edge["to"], i, boxes)
            left, right = sorted((sx, tx))
            # Earlier lanes have vertical segments through this label band.
            # Place the label in a clear interval and measure that exact width.
            cuts = [left] + sorted({p for p in ports if left < p < right}) + [right]
            start, end = max(zip(cuts, cuts[1:]), key=lambda pair: pair[1] - pair[0])
            label_width = end - start - 12
            routes.append(dict(source=sx, target=tx, label_x=start + 6, label_width=label_width,
                               height=self.height(edge.get("label", ""), label_width, 13) + 14))
            ports.extend((sx, tx))
        return routes

    @staticmethod
    def edge_port(node, node_id, edge_index, boxes):
        node_index = next(i for i, item in enumerate(node["items"]) if item["id"] == node_id)
        incident = [i for i, edge in enumerate(node["edges"]) if node_id in (edge["from"], edge["to"])]
        x, _, width, _ = boxes[node_index]
        ratio = .5 if len(incident) == 1 else .2 + .6 * incident.index(edge_index) / (len(incident) - 1)
        return x + width * ratio

    def edge_space(self, node, width):
        return sum(route["height"] for route in self.edge_routes(node, width)) + 24


def walk(node):
    if "layout" in node:
        for child in node["children"]:
            yield from walk(child)
    else:
        yield node


def validate_blocks(content, outline=""):
    def validate_group(node):
        if "layout" not in node:
            return
        if "columns" in node and node["layout"] != "grid":
            raise CompositionError("composition_unused_field", "columns", "columns applies only to grid")
        if "weights" in node and node["layout"] == "grid":
            raise CompositionError("composition_unused_field", "weights", "grid uses equal columns, not weights")
        for child in node["children"]:
            validate_group(child)
    validate_group(content["composition"])
    nodes = list(walk(content["composition"]))
    if len(nodes) > 24:
        raise CompositionError("composition_block_limit", "composition", "Use at most 24 blocks per page")
    ids = [n["id"] for n in nodes]
    if len(ids) != len(set(ids)):
        raise CompositionError("composition_duplicate_id", "composition", "Use unique block IDs")
    for node in nodes:
        mode = BLOCKS[node["blockRef"]]
        common = {"id", "blockRef", "tone", "align", "title", "body", "note"}
        specific = ({"items", "direction"} if mode == "flow" else
                    {"items", "edges"} if mode == "network" else
                    {"items", "unit", "scaleMax", "independent"} if mode == "bars" else
                    {"items"} if mode in {"list", "layers", "timeline"} else
                    {"columns", "rows"} if mode == "table" else
                    {"value", "unit"} if mode == "metric" else set())
        unused = set(node) - common - specific
        if unused:
            raise CompositionError("composition_unused_field", node["id"], "This renderer does not consume: " + ", ".join(sorted(unused)))
        if not node["title"].strip() and not node.get("body", "").strip() and not specific:
            raise CompositionError("composition_empty_block", node["id"], "Provide visible content")
        for item in node.get("items", []):
            if not item["label"].strip():
                raise CompositionError("composition_empty_label", node["id"], "Provide a non-empty item label")
            unused_item = set(item) - ({"id", "label", "value"} if mode == "bars" else {"id", "label", "detail"})
            if unused_item:
                raise CompositionError("composition_unused_field", node["id"], "Item field not consumed: " + ", ".join(sorted(unused_item)))
        fields = (["items"] if mode in {"list", "layers", "flow", "network", "timeline", "bars"} else
                  ["columns", "rows"] if mode == "table" else ["value"] if mode == "metric" else ["body"] if mode == "code" else [])
        for field in fields:
            if not node.get(field):
                raise CompositionError("composition_required_field", node["id"] + "." + field, "Required by this block renderer")
        if mode == "network":
            item_ids = [i.get("id") for i in node["items"]]
            if None in item_ids or len(set(item_ids)) != len(item_ids):
                raise CompositionError("composition_node_ids", node["id"], "Every node needs a unique id")
            if not node.get("edges") or any(e["from"] not in item_ids or e["to"] not in item_ids or e["from"] == e["to"] for e in node["edges"]):
                raise CompositionError("composition_edge_reference", node["id"], "Use explicit edges between different existing nodes")
        if mode == "bars":
            if not node.get("unit") or not node.get("scaleMax"):
                raise CompositionError("composition_scale_required", node["id"], "Declare unit and scaleMax from actual data")
            if any("value" not in i or not math.isfinite(i["value"]) or not 0 <= i["value"] <= node["scaleMax"] for i in node["items"]):
                raise CompositionError("composition_value_range", node["id"], "All values must be provided within the declared scale; no invented zeroes")
        if mode == "table" and any(len(row) != len(node["columns"]) for row in node["rows"]):
            raise CompositionError("composition_table_width", node["id"], "Every row must match the columns")
        if node["blockRef"] == "X02":
            import json
            try:
                json.loads(node["body"])
            except ValueError as exc:
                raise CompositionError("composition_invalid_json", node["id"], "Preserve complete, valid JSON in body") from exc
    # Explicit cardinality in the user-confirmed outline is authoritative. This
    # narrow check complements, and does not pretend to replace, semantic QA.
    numerals = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    for match in re.finditer(r"(?<![\d一二三四五六七八九十])([1-9]\d?|[一二两三四五六七八九十])\s*(?:个)?层(?:技术|架构|结构|模型|分层|栈|[：:／/\s]|$)", outline):
        raw = match.group(1)
        expected = int(raw) if raw.isdigit() else numerals[raw]
        layers = [n for n in nodes if BLOCKS[n["blockRef"]] == "layers"]
        actual = sum(len(n["items"]) for n in layers)
        if actual != expected:
            raise CompositionError("composition_outline_count_mismatch", "composition",
                                   f"Confirmed outline requires {expected} layers; received {actual}. Preserve the outline using A02, do not pad or reclassify.")


def compose(content, layout=None, *, outline="", page_number=None, font_root=None):
    content = {k: v for k, v in content.items() if not k.startswith("__")}
    error = next(Draft202012Validator(composition_schema(layout)).iter_errors(content), None)
    if error:
        raise CompositionError("composition_schema_invalid", ".".join(map(str, error.absolute_path)), "Use the returned block composition schema")
    validate_blocks(content, outline)
    c = Composer(font_root)
    page = []
    c.rect(page, "page-background", 0, 0, 1280, 720, PAPER, 0)
    c.rect(page, "page-accent", 64, 32, 4, 36, ACCENT, 0)
    title_h = c.text(page, "page-title", content["title"], 88, 24, 1128, 34, bold=True, max_height=100)
    top = 24 + title_h + 24
    if content.get("eyebrow"):
        top += c.text(page, "page-eyebrow", content["eyebrow"], 88, top - 10, 1128, 17, ACCENT) + 12
    c.text(page, "page-footer", content.get("footer", ""), 64, 680, 1010, 13, max_height=26)
    c.text(page, "page-number", str(page_number) if page_number is not None else "", 1150, 680, 66, 13)
    c.components.append({"id": "page-chrome", "description": "Page title and footer", "position": {"x": 0, "y": 0}, "elements": page})
    c.place(content["composition"], 64, max(110, top), 1152, 660 - max(110, top))
    return {"id": (layout or {}).get("id", "graphite-compose"), "description": "Native block composition",
            "components": c.components}
