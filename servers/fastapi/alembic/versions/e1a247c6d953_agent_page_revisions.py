"""Add per-slide versions without changing existing presentation content."""
from alembic import op
import sqlalchemy as sa

revision = "e1a247c6d953"
down_revision = "d9f136b5c842"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_page_revisions",
        sa.Column("slide_id", sa.Uuid(), sa.ForeignKey("slides.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("presentations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    for field in ("document_id", "owner_id"):
        op.create_index(f"ix_agent_page_revisions_{field}", "agent_page_revisions", [field])
    # Existing managed pages start at zero. Subsequent native/workflow edits
    # increment once per changed page in the same transaction as its content.
    inspector = sa.inspect(op.get_bind())
    if (inspector.has_table("slides")
            and {"id", "presentation"} <= {c["name"] for c in inspector.get_columns("slides")}
            and {"id", "owner_id", "agent_managed"} <= {c["name"] for c in inspector.get_columns("presentations")}):
        # Partially initialized legacy databases may not contain native pages yet.
        op.execute(sa.text("""INSERT INTO agent_page_revisions (slide_id, document_id, owner_id, revision)
            SELECT s.id, s.presentation, p.owner_id, 0 FROM slides s
            JOIN presentations p ON p.id = s.presentation
            WHERE p.agent_managed = true AND p.owner_id IS NOT NULL"""))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM agent_page_revisions WHERE revision > 0")):
        raise RuntimeError("Keep page revisions while versioned edits exist; roll back application code without dropping this table")
    op.drop_table("agent_page_revisions")
