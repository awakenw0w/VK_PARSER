"""Add administrator-managed bot access.

Revision ID: 0005_user_access
Revises: 0004_random_city_draws
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_user_access"
down_revision: str | None = "0004_random_city_draws"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("has_access", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "has_access")
