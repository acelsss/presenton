"""External Agent PPT task control records; native drafts remain in their tables."""

from alembic import op
import sqlalchemy as sa

revision = "17a9c2e4f6b8"
down_revision = "026c0ba8b35c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("agent_ppt_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope_hash", sa.String(64), nullable=False, index=True),
        sa.Column("create_key", sa.String(160), nullable=False),
        sa.Column("create_hash", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("presentation_id", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.String(200), nullable=False),
        sa.Column("template_snapshot", sa.JSON(), nullable=False),
        sa.Column("writer_id", sa.String(160)),
        sa.Column("writer_kind", sa.String(16)),
        sa.Column("writer_run_id", sa.String(160)),
        sa.Column("writer_until", sa.Integer(), nullable=False),
        sa.Column("writer_slide_ids", sa.JSON()),
        sa.Column("handoff_required", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False, index=True),
        sa.Column("delivery_id", sa.Uuid(), index=True),
        sa.Column("delivery", sa.JSON()),
        sa.Column("cleanup_state", sa.String(16), nullable=False),
        sa.UniqueConstraint("scope_hash", "create_key"),
    )
    op.create_table("agent_ppt_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("request_key", sa.String(160), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("job_id", "request_key"),
    )
    op.create_table("agent_ppt_editor_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False, index=True),
        sa.Column("consumed", sa.Boolean(), nullable=False),
    )
    op.create_table("agent_ppt_objects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("relative_path", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
    )


def downgrade():
    for table in ("agent_ppt_objects", "agent_ppt_editor_sessions", "agent_ppt_operations", "agent_ppt_jobs"):
        op.drop_table(table)
