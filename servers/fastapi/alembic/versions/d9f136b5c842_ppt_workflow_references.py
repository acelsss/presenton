"""Add durable business references and generation gate."""
from alembic import op
import sqlalchemy as sa

revision = "d9f136b5c842"
down_revision = "c8e025a4b731"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_documents", sa.Column("workflow_task_id", sa.String(), nullable=True))
    op.create_table(
        "ppt_workflow_refs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("document_id", sa.Uuid()),
        sa.Column("task_id", sa.String()),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("request_hash", sa.String(64)),
        sa.Column("result", sa.JSON()),
    )
    for field in ("owner_id", "document_id", "task_id"):
        op.create_index(f"ix_ppt_workflow_refs_{field}", "ppt_workflow_refs", [field])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM ppt_workflow_refs")):
        raise RuntimeError("Archive workflow references and receipts before downgrade")
    op.drop_table("ppt_workflow_refs")
    op.drop_column("agent_documents", "workflow_task_id")
