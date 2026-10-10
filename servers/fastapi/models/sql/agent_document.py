"""Coordination records for the optional external-agent API."""

import uuid
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


class AgentCallerSession(SQLModel, table=True):
    __tablename__ = "agent_caller_sessions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    token_hash: str = Field(unique=True, max_length=64)
    label: str = Field(max_length=80)
    expires_at: int


class AgentDocument(SQLModel, table=True):
    __tablename__ = "agent_documents"

    id: uuid.UUID = Field(
        primary_key=True, foreign_key="presentations.id", ondelete="CASCADE"
    )
    owner_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    revision: int = Field(default=0)
    phase: str = Field(default="outline", max_length=32)
    # Inert legacy columns retained for non-destructive upgrades/rollback.
    # No runtime code uses these fields to authorize mutations.
    writer_session_id: uuid.UUID | None = Field(
        default=None, foreign_key="agent_caller_sessions.id"
    )
    writer_epoch: int = Field(default=0)
    writer_expires_at: int = Field(default=0)
    template_id: str | None = Field(default=None)
    workflow_task_id: str | None = Field(default=None)


class AgentPageRevision(SQLModel, table=True):
    """Monotonic versions for independent edits to stable slide IDs."""
    __tablename__ = "agent_page_revisions"

    slide_id: uuid.UUID = Field(primary_key=True, foreign_key="slides.id", ondelete="CASCADE")
    document_id: uuid.UUID = Field(foreign_key="presentations.id", ondelete="CASCADE", index=True)
    owner_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    revision: int = Field(default=0)


class AgentOperationReceipt(SQLModel, table=True):
    __tablename__ = "agent_operation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "operation_id", name="uq_agent_operation_owner_key"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    operation_id: uuid.UUID = Field(index=True)
    # Retained after document removal: a lost create response must never recreate it.
    document_id: uuid.UUID
    payload_hash: str = Field(max_length=64)
    result: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: int
