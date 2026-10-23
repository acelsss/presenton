"""Disable native model execution without changing authentication or editor access."""
from fastapi import HTTPException, Request

from services.agent_tools.config import external_agent_mode


# Endpoint identities, not HTTP verbs: several GET streams execute models too.
GENERATION_ENDPOINTS = {
    "chat": {"chat_message", "chat_message_stream"},
    "slide": {"edit_slide", "edit_slide_html"},
    "outlines": {"stream_outlines"},
    "presentation": {
        "prepare_presentation", "stream_presentation", "generate_presentation_sync",
        "generate_presentation_async", "edit_presentation_with_new_content",
        "derive_presentation_from_existing_one", "generate_smart_presentation_sync",
        "generate_smart_presentation_async",
    },
    "images": {"generate_image"},
    "template": {
        "create_template", "retry_create_template",
        "generate_template_layout_from_prompt", "create_template_slide_layouts",
        "generate_template_blocks",
    },
    "theme_generate": {"generate_theme_v3"},
}


async def require_native_capability(request: Request):
    if not external_agent_mode():
        return
    endpoint = request.scope["route"].endpoint
    module = endpoint.__module__.rsplit(".", 1)[-1]
    if endpoint.__name__ in GENERATION_ENDPOINTS.get(module, set()):
        raise HTTPException(409, {
            "code": "internal_generation_disabled",
            "message": "Use the external agent for generation. Native viewing and manual editing remain available.",
            "retryable": False,
        })
