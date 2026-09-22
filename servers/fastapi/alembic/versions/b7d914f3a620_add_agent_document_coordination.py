"""Add opt-in external-agent document coordination.

Revision ID: b7d914f3a620
Revises: 026c0ba8b35c
"""

from alembic import op
import sqlalchemy as sa

revision = "b7d914f3a620"
down_revision = "026c0ba8b35c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "presentations",
        sa.Column(
            "agent_managed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_index(
        "ix_presentations_agent_managed", "presentations", ["agent_managed"]
    )
    op.create_table(
        "agent_caller_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_agent_caller_sessions_owner_id", "agent_caller_sessions", ["owner_id"]
    )
    op.create_table(
        "agent_documents",
        sa.Column(
            "id",
            sa.Uuid(),
            sa.ForeignKey("presentations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column(
            "writer_session_id", sa.Uuid(), sa.ForeignKey("agent_caller_sessions.id")
        ),
        sa.Column("writer_epoch", sa.Integer(), nullable=False),
        sa.Column("writer_expires_at", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.String()),
    )
    op.create_index("ix_agent_documents_owner_id", "agent_documents", ["owner_id"])
    op.create_table(
        "agent_operation_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "owner_id", "operation_id", name="uq_agent_operation_owner_key"
        ),
    )
    op.create_index(
        "ix_agent_operation_receipts_owner_id", "agent_operation_receipts", ["owner_id"]
    )
    op.create_index(
        "ix_agent_operation_receipts_operation_id",
        "agent_operation_receipts",
        ["operation_id"],
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM agent_operation_receipts")
    ) or connection.scalar(
        sa.text("SELECT count(*) FROM presentations WHERE agent_managed = true")
    ):
        raise RuntimeError(
            "Managed documents/receipts exist; disable the API instead of dropping coordination data"
        )
    op.drop_table("agent_operation_receipts")
    op.drop_table("agent_documents")
    op.drop_table("agent_caller_sessions")
    op.drop_index("ix_presentations_agent_managed", table_name="presentations")
    op.drop_column("presentations", "agent_managed")
