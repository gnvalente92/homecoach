"""Store the duration of a proposed strength session."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("coach_suggestions", sa.Column("duration_min", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("coach_suggestions", "duration_min")
