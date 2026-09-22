"""Opaque references; execution state lives in async_tasks.payload."""
import secrets
import uuid
from typing import Any
from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class PptWorkflowRef(SQLModel, table=True):
    __tablename__ = "ppt_workflow_refs"
    id: str = Field(default_factory=lambda: secrets.token_urlsafe(32), primary_key=True)
    owner_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    actor: str = Field(max_length=64)
    kind: str = Field(max_length=24)
    # Retained after document deletion so replay cannot recreate it.
    document_id: uuid.UUID | None = Field(default=None, index=True)
    task_id: str | None = Field(default=None, index=True)
    expires_at: int
    data: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    request_hash: str | None = Field(default=None, max_length=64)
    result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
