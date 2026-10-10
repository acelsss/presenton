"""Immutable owner-scoped three-layer template versions."""
from alembic import op
import sqlalchemy as sa

revision = "f2b358d7ea64"
down_revision = "e1a247c6d953"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("template_pack_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("template_id", sa.String(), sa.ForeignKey("template_v2.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("source_hash", sa.String(), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("asset_directory", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    for column in ("template_id", "owner_id", "source_hash"):
        op.create_index(f"ix_template_pack_versions_{column}", "template_pack_versions", [column])


def downgrade():
    op.drop_table("template_pack_versions")
