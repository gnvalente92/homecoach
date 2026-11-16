"""Rules instead of the local AI: structured check-ins; drop coach suggestions, coach notes and race insights."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("checkins", sa.Column("feeling", sa.String(12), nullable=True))
    op.add_column("checkins", sa.Column("until", sa.Date, nullable=True))
    for t in ("race_insights", "coach_notes", "coach_suggestions"):
        op.drop_table(t)


def downgrade() -> None:
    # The tables come back empty: the dropped AI suggestions, notes and insights are not restored.
    op.create_table(
        "coach_suggestions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("session", sa.String(80)),
        sa.Column("km", sa.Float),
        sa.Column("pace", sa.String(40)),
        sa.Column("detail", sa.Text),
        sa.Column("move_to", sa.Date),
        sa.Column("duration_min", sa.Integer),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("source", sa.String(10), nullable=False, server_default="chat"),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending", index=True),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "coach_notes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False, index=True),
    )
    op.create_table(
        "race_insights",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("race_id", sa.Integer, sa.ForeignKey("races.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("facts", sa.JSON),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.drop_column("checkins", "until")
    op.drop_column("checkins", "feeling")
