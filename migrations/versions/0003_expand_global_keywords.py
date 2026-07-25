"""Expand the initial global keyword dictionary.

Revision ID: 0003_expand_global_keywords
Revises: 0002_normalized_community_names
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from vk_chat_bot.text import normalize_text

revision: str = "0003_expand_global_keywords"
down_revision: str | None = "0002_normalized_community_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GLOBAL_KEYWORDS = (
    "работа",
    "подработка",
    "шабашка",
    "доска объявлений",
    "услуги",
    "взаимопомощь",
    "барахолка",
    "куплю",
    "продам",
    "куплю-продам",
    "ярмарка",
    "подслушано",
    "новости",
)


def upgrade() -> None:
    connection = op.get_bind()
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
    for value in GLOBAL_KEYWORDS:
        normalized = normalize_text(value)
        if normalized not in existing:
            connection.execute(
                keywords.insert().values(
                    owner_user_id=None,
                    scope="global",
                    text=value,
                    normalized_text=normalized,
                    enabled=True,
                    created_at=sa.func.current_timestamp(),
                )
            )
            existing.add(normalized)


def downgrade() -> None:
    added_keywords = GLOBAL_KEYWORDS[3:]
    normalized = [normalize_text(value) for value in added_keywords]
    connection = op.get_bind()
    keywords = sa.table(
        "keywords",
        sa.column("scope", sa.String()),
        sa.column("normalized_text", sa.String()),
    )
    connection.execute(
        keywords.delete().where(
            keywords.c.scope == "global",
            keywords.c.normalized_text.in_(normalized),
        )
    )
