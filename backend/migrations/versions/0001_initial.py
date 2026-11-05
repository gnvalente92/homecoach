"""initial schema

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "activities",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("provider", sa.String(20), nullable=False, index=True),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("sport", sa.String(64), nullable=False),
        sa.Column("type", sa.String(20), nullable=False, index=True),
        sa.Column("name", sa.String(200)),
        sa.Column("start_time", TS, nullable=False, index=True),
        sa.Column("local_date", sa.Date, nullable=False, index=True),
        sa.Column("duration_s", sa.Integer),
        sa.Column("distance_m", sa.Float),
        sa.Column("avg_hr", sa.Integer),
        sa.Column("max_hr", sa.Integer),
        sa.Column("calories", sa.Integer),
        sa.Column("training_load", sa.Float),
        sa.Column("fit_path", sa.Text),
        sa.Column("raw", sa.JSON),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "daily_metrics",
        sa.Column("day", sa.Date, primary_key=True),
        *[sa.Column(c, sa.Integer) for c in ("steps", "active_kcal", "exercise_min", "stress_avg", "resting_hr")],
        *[sa.Column(c, sa.Float) for c in ("hrv_avg", "hrv_lo", "hrv_hi", "training_load", "load_short", "load_long", "load_ratio")],
        *[sa.Column(c, sa.Integer) for c in ("sleep_min", "deep_min", "light_min", "rem_min", "awake_min", "sleep_score")],
        sa.Column("sleep_start", TS),
        sa.Column("raw", sa.JSON),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "fitness_snapshots",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("vo2max", sa.Float),
        sa.Column("threshold_pace_s", sa.Integer),
        sa.Column("running_level", sa.Float),
        sa.Column("predictions", sa.JSON),
    )
    op.create_table(
        "races",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("distance_km", sa.Float, nullable=False),
        sa.Column("goal_time_s", sa.Integer),
        sa.Column("priority", sa.String(1), nullable=False, server_default="A"),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "sync_state",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON, nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    for t in ("sync_state", "races", "fitness_snapshots", "daily_metrics", "activities"):
        op.drop_table(t)
