"""race insights: the coach's reading of each race outlook

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "race_insights",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("race_id", sa.Integer, sa.ForeignKey("races.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("facts", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("race_insights")
