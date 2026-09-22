"""Preserve deployed Agent PPT history while adopting external-agent documents.

This merge retains legacy task tables and data; it does not enable legacy tools.
"""

revision = "c8e025a4b731"
down_revision = ("b7d914f3a620", "29b8d3a6c1e0")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
