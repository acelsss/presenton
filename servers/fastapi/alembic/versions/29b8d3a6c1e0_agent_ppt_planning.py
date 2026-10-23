"""Task-private planning. Existing direct tasks keep a null plan."""
from alembic import op
import sqlalchemy as sa

revision = "29b8d3a6c1e0"
down_revision = "17a9c2e4f6b8"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("agent_ppt_jobs") as batch:
        batch.add_column(sa.Column("planning", sa.JSON(), nullable=True))
        batch.alter_column("template_id", existing_type=sa.String(200), nullable=True)
        batch.alter_column("template_snapshot", existing_type=sa.JSON(), nullable=True)


def downgrade():
    # Never silently discard active guided work during a rollback.
    if op.get_bind().execute(sa.text("SELECT count(*) FROM agent_ppt_jobs WHERE planning IS NOT NULL")).scalar():
        raise RuntimeError("Finish or cancel and clean guided tasks before downgrade")
    with op.batch_alter_table("agent_ppt_jobs") as batch:
        batch.drop_column("planning")
        batch.alter_column("template_id", existing_type=sa.String(200), nullable=False)
        batch.alter_column("template_snapshot", existing_type=sa.JSON(), nullable=False)
