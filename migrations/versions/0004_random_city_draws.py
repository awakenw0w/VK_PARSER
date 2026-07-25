"""Remember random city draws separately for every user.

Revision ID: 0004_random_city_draws
Revises: 0003_expand_global_keywords
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_random_city_draws"
down_revision: str | None = "0003_expand_global_keywords"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "random_city_draws",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "city_id",
            sa.Integer(),
            sa.ForeignKey("cities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("drawn_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "city_id", name="uq_random_city_draw_user_city"),
    )
    op.create_index("ix_random_city_draws_user_id", "random_city_draws", ["user_id"])
    op.create_index("ix_random_city_draws_city_id", "random_city_draws", ["city_id"])


def downgrade() -> None:
    op.drop_index("ix_random_city_draws_city_id", table_name="random_city_draws")
    op.drop_index("ix_random_city_draws_user_id", table_name="random_city_draws")
    op.drop_table("random_city_draws")
