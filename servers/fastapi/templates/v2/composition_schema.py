"""Versioned, constrained block composition; never arbitrary HTML or native UI."""
import copy

RENDERER = "native-blocks-v1"
# The contracts remain assets. Only these implemented renderers are advertised.
BLOCKS = {
    "H01": "hero", "E01": "text", "E02": "text",
    "E04": "text", "C01": "text", "V01": "quote", "V04": "text",
    "S01": "text", "S03": "text", "L01": "list", "L03": "list",
    "L04": "list", "A02": "layers", "P01": "flow", "P02": "flow",
    "A03": "network", "R01": "timeline", "D01": "bars", "M01": "metric",
    "T01": "table", "X01": "code", "X02": "code",
}


def is_composition(layout):
    return isinstance(layout, dict) and layout.get("renderer") == RENDERER


def obj(properties, required=()):
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(required)}


def string(limit=1500):
    return {"type": "string", "maxLength": limit}


def composition_schema(layout=None):
    item = obj({"id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,40}$"},
                "label": string(160), "detail": string(700), "value": {"type": "number"}}, ["label"])
    block = obj({
        "id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,40}$"},
        "blockRef": {"enum": list(BLOCKS)},
        "tone": {"enum": ["plain", "surface", "ink", "accent"]},
        "align": {"enum": ["start", "center"]},
        "title": string(180), "body": string(2200), "note": string(700),
        "items": {"type": "array", "minItems": 1, "maxItems": 16, "items": item},
        "direction": {"enum": ["row", "column"]},
        "edges": {"type": "array", "maxItems": 32, "items": obj({
            "from": string(40), "to": string(40), "label": string(100)}, ["from", "to"])},
        "value": string(40), "unit": string(40),
        "scaleMax": {"type": "number", "exclusiveMinimum": 0},
        "independent": {"type": "boolean"},
        "columns": {"type": "array", "minItems": 1, "maxItems": 6, "items": string(100)},
        "rows": {"type": "array", "minItems": 1, "maxItems": 12,
                 "items": {"type": "array", "minItems": 1, "maxItems": 6, "items": string(300)}},
    }, ["id", "blockRef", "title"])
    # Bounded depth keeps both tool schemas and layout computation manageable.
    node = block
    for _ in range(3):
        group = obj({"layout": {"enum": ["row", "column", "grid"]},
                     "gap": {"type": "number", "minimum": 12, "maximum": 48},
                     "columns": {"type": "integer", "minimum": 1, "maximum": 4},
                     "weights": {"type": "array", "minItems": 1, "maxItems": 8,
                                 "items": {"type": "number", "minimum": 0.2, "maximum": 5}},
                     "children": {"type": "array", "minItems": 1, "maxItems": 8, "items": node}},
                    ["layout", "children"])
        node = {"oneOf": [block, group]}
    schema = obj({"title": string(240), "eyebrow": string(70), "footer": string(200),
                  "composition": node}, ["title", "composition"])
    schema["title"] = (layout or {}).get("id", "graphite-compose")
    schema["description"] = (
        "Native editable block composition. Choose semantic blocks and row/column/grid relationships. "
        "Array cardinality comes from the confirmed outline; never invent items to fill space. "
        "A02=layers, A03=nodes+explicit edges, P01/P02=flow, R01=timeline, L01/L03/L04=list, "
        "D01=bars(items.value, unit, scaleMax), T01=table(columns, rows), X01/X02=code(body). "
        "id is unique per page. Font measurement rejects overflow; revise content/layout, do not shrink type. "
        "There are no fixed page slots. Fields are display content; preserve semantic conditions in body/note."
    )
    return copy.deepcopy(schema)
