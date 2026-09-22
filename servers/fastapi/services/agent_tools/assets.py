"""Bind existing assets without model calls, downloads or placeholder substitution."""

from pathlib import Path
from typing import Any

from sqlalchemy import select
from api.v1.auth.assets import normalized_app_data_parts
from api.v1.auth.context import reset_current_owner_id, set_current_owner_id

from models.sql.image_asset import ImageAsset
from services.agent_tools.errors import OperationRejected
from utils.asset_directory_utils import (
    normalize_slide_asset_url,
    resolve_app_path_to_filesystem,
)


def asset_references(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from asset_references(item)
    elif isinstance(value, dict):
        if value.get("type") == "image":
            yield value.get("data")
        for key, item in value.items():
            if key in {"image_url", "icon_url", "__image_url__", "__icon_url__"}:
                yield item
            elif key == "url" and isinstance(item, str):
                # Also covers infographic icons. Do not fetch arbitrary references.
                yield item
            elif key != "data" or value.get("type") != "image":
                yield from asset_references(item)


class ExistingAssets:
    def __init__(self, allowed: set[str]):
        self.allowed = allowed

    @staticmethod
    def _exists(url, owner_id):
        # Native path resolution remains the authority, including traversal checks.
        # Bind ownership explicitly for non-HTTP callers as well.
        token = set_current_owner_id(owner_id)
        try:
            resolved = resolve_app_path_to_filesystem(url)
            return bool(resolved and Path(resolved).is_file())
        finally:
            reset_current_owner_id(token)

    @classmethod
    async def for_document(cls, session, owner_id, template_layout):
        assets = await session.scalars(
            select(ImageAsset).where(ImageAsset.owner_id == owner_id)
        )
        allowed = set()
        for asset in assets:
            url = normalize_slide_asset_url(asset.path)
            if cls._exists(url, owner_id):
                allowed.add(url)
        # The native template importer copies bundled files into app_data/templates.
        # Only references already in this document's template snapshot are shared.
        for reference in asset_references(template_layout):
            parts = normalized_app_data_parts(reference)
            template_static = bool(
                isinstance(reference, str)
                and reference.startswith("/app_data/templates/")
                and parts
                and len(parts) >= 4
                and parts[0] == "templates"
                and parts[2] == "static"
            )
            if isinstance(reference, str) and (
                reference.startswith("/static/") or template_static
            ):
                if cls._exists(reference, owner_id):
                    allowed.add(normalize_slide_asset_url(reference))
        return cls(allowed)

    async def validate(self, value: Any) -> None:
        for reference in asset_references(value):
            if (
                not isinstance(reference, str)
                or normalize_slide_asset_url(reference) not in self.allowed
            ):
                raise OperationRejected("asset_missing_or_forbidden")
