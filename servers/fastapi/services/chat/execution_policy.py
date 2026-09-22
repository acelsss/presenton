"""Explicit native-tool extension seams; the interactive chat keeps its defaults."""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class ChatExecutionPolicy:
    managed_transaction: bool = False
    allow_generation: bool = True
    use_memory: bool = True
    current_ui_reads: bool = False
    preserve_slide_ids: bool = False
    preserve_ui_metadata: bool = False
    soft_text_lengths: bool = False
    validate_assets: Callable[[Any], Awaitable[None]] | None = None


def project_slide_ui(ui: Any) -> list[dict[str, Any]]:
    """Use native element discovery, with the same text precedence as the canvas."""
    from services.chat.slide_ui_helpers import (
        _collect_editable_elements,
        _element_content,
        _resolve_element_path,
    )

    if not isinstance(ui, dict):
        return []
    result = []
    for item in _collect_editable_elements(ui, include_visual_elements=True):
        path = item["path"]
        element = _resolve_element_path(ui, path)
        content = _element_content(element)
        if element.get("type") == "text" and isinstance(element.get("text"), str):
            content = {"text": element["text"]}
        result.append({"path": path, "type": element.get("type"), "content": content})
    return result


def soften_content_lengths(schema: Any, field_name: str = "") -> Any:
    """Only native template content schemas, never tool IDs or protocol schemas."""
    if isinstance(schema, list):
        return [soften_content_lengths(value, field_name) for value in schema]
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key in {
            "properties",
            "$defs",
            "definitions",
            "patternProperties",
        } and isinstance(value, dict):
            result[key] = {
                name: soften_content_lengths(child, name)
                for name, child in value.items()
            }
        elif key in {"minLength", "maxLength"} and field_name not in {
            "id",
            "parent_id",
            "url",
            "image_url",
            "icon_url",
        }:
            result[f"x-layout-{key}"] = value
        else:
            result[key] = soften_content_lengths(value, field_name)
    return result


def existing_asset_schema(schema: Any) -> Any:
    """Extend native image fields with an explicit, required existing asset URL."""
    if isinstance(schema, list):
        return [existing_asset_schema(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    result = {key: existing_asset_schema(value) for key, value in schema.items()}
    properties = result.get("properties", {})
    for prompt, url in (("image_prompt", "image_url"), ("icon_query", "icon_url")):
        if prompt in properties:
            properties[url] = {"type": "string", "minLength": 1}
            result["required"] = [
                key for key in result.get("required", []) if key != prompt
            ] + [url]
    return result
