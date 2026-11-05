"""Recurring distance, time and session goals."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "training_goals",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("sport", sa.String(20), nullable=False),
        sa.Column("metric", sa.String(20), nullable=False),
        sa.Column("target", sa.Float, nullable=False),
        sa.UniqueConstraint("period", "sport", "metric", name="uq_training_goal"),
    )


def downgrade() -> None:
    op.drop_table("training_goals")
