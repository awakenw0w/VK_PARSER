"""Normalize community names and seed the initial global dictionary once.

Revision ID: 0002_normalized_community_names
Revises: 0001_initial
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from vk_chat_bot.text import normalize_text

revision: str = "0002_normalized_community_names"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("communities", sa.Column("normalized_name", sa.String(255), nullable=True))
    connection = op.get_bind()
    communities = sa.table(
        "communities",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("normalized_name", sa.String()),
    )
    for row in connection.execute(sa.select(communities.c.id, communities.c.name)):
        connection.execute(
            communities.update()
            .where(communities.c.id == row.id)
            .values(normalized_name=normalize_text(row.name or ""))
        )
    with op.batch_alter_table("communities") as batch:
        batch.alter_column("normalized_name", existing_type=sa.String(255), nullable=False)
        batch.create_index("ix_communities_normalized_name", ["normalized_name"])

    keywords = sa.table(
        "keywords",
        sa.column("owner_user_id", sa.Integer()),
        sa.column("scope", sa.String()),
        sa.column("text", sa.String()),
        sa.column("normalized_text", sa.String()),
        sa.column("enabled", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    existing = set(
        connection.execute(
            sa.select(keywords.c.normalized_text).where(keywords.c.scope == "global")
        ).scalars()
    )
    now = sa.func.current_timestamp()
    for value in ("работа", "подработка", "шабашка"):
        normalized = normalize_text(value)
        if normalized not in existing:
            connection.execute(
                keywords.insert().values(
                    owner_user_id=None,
                    scope="global",
                    text=value,
                    normalized_text=normalized,
                    enabled=True,
                    created_at=now,
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("communities") as batch:
        batch.drop_index("ix_communities_normalized_name")
        batch.drop_column("normalized_name")
