"""coach: check-ins, suggestions, notes

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "checkins",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
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


def downgrade() -> None:
    for t in ("coach_notes", "coach_suggestions", "checkins"):
        op.drop_table(t)
