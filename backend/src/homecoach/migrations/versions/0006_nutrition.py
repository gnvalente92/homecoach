"""Nutrition: body profile and food log."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nutrition_profile",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("weight_kg", sa.Float),
        sa.Column("height_cm", sa.Float),
        sa.Column("birth_date", sa.Date),
        sa.Column("sex", sa.String(10)),
        sa.Column("goal", sa.String(10), nullable=False, server_default="maintain"),
        sa.Column("protein_g_per_kg", sa.Float, nullable=False, server_default="1.8"),
        sa.Column("follow_provider", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(20)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "food_entries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("day", sa.Date, nullable=False, index=True),
        sa.Column("meal", sa.String(10), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("quantity", sa.String(60)),
        sa.Column("kcal", sa.Float, nullable=False),
        sa.Column("protein_g", sa.Float, nullable=False),
        sa.Column("carbs_g", sa.Float, nullable=False),
        sa.Column("fat_g", sa.Float, nullable=False),
        sa.Column("source", sa.String(10), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("food_entries")
    op.drop_table("nutrition_profile")
