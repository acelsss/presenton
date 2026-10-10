"""Derive workflow choices from the same native V2 schemas used by saveSlide."""
from services.chat.execution_policy import existing_asset_schema, soften_content_lengths
from services.chat.memory_layer import PresentationChatMemoryLayer


def layouts(snapshot):
    model = PresentationChatMemoryLayer._build_template_layout_model(
        snapshot, layout_name=str(snapshot.get("name", "template"))
    )
    return {layout.id: layout for layout in model.slides}


def schema_for(layout):
    return existing_asset_schema(soften_content_lengths(layout.json_schema))


def constraints(schema, path="$", required=True):
    """Expose structural/cardinality and asset requirements, including conditional ones."""
    result = []
    for name, child in schema.get("properties", {}).items():
        needed = required and name in schema.get("required", [])
        if name in {"image_url", "icon_url", "__image_url__", "__icon_url__"}:
            result.append({"path": f"{path}.{name}", "asset": name, "required": needed})
        result.extend(constraints(child, f"{path}.{name}", needed))
    if schema.get("type") == "array":
        result.append({"path": path, "minItems": schema.get("minItems", 0),
                       "maxItems": schema.get("maxItems"), "required": required})
        result.extend(constraints(schema.get("items", {}), path + "[]", required and schema.get("minItems", 0) > 0))
    for kind in ("oneOf", "anyOf", "allOf"):
        for index, child in enumerate(schema.get(kind, [])):
            result.extend(constraints(child, f"{path}<{kind}:{index}>", required and kind == "allOf"))
    return result


def shape(schema):
    """A structural guide only; never silently used as candidate page content."""
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if schema.get("type") == "object" or "properties" in schema:
        return {key: shape(value) for key, value in schema.get("properties", {}).items()
                if key in schema.get("required", [])}
    if schema.get("type") == "array":
        return [shape(schema.get("items", {})) for _ in range(min(schema.get("minItems", 1), 50))]
    if schema.get("type") in {"integer", "number"}:
        return schema.get("minimum", 0)
    if schema.get("type") == "boolean":
        return False
    return "<填写内容或已有素材 URL>"


def describe(layout, full=False):
    schema = schema_for(layout)
    result = {"layoutId": layout.id, "description": layout.description,
              "constraints": constraints(schema)}
    if full:
        result.update(schema=schema, contentShape=shape(schema),
                      shapeIsContent=False, textLengthsAreLayoutHints=True)
    return result
