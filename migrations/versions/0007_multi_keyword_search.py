"""Store multiple keywords in one search run.

Revision ID: 0007_multi_keyword_search
Revises: 0006_multi_city_search
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from vk_chat_bot.text import normalize_text

revision: str = "0007_multi_keyword_search"
down_revision: str | None = "0006_multi_city_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "search_runs",
        sa.Column("keywords_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "search_results",
        sa.Column("matched_keywords_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "search_results",
        sa.Column("matched_keywords_normalized", sa.Text(), nullable=False, server_default=""),
    )

    connection = op.get_bind()
    search_runs = sa.table(
        "search_runs",
        sa.column("id", sa.Integer()),
        sa.column("keyword", sa.String()),
        sa.column("keywords_json", sa.Text()),
    )
    search_results = sa.table(
        "search_results",
        sa.column("id", sa.Integer()),
        sa.column("search_run_id", sa.Integer()),
        sa.column("matched_keywords_json", sa.Text()),
        sa.column("matched_keywords_normalized", sa.Text()),
    )
    for row in connection.execute(sa.select(search_runs.c.id, search_runs.c.keyword)):
        keyword = str(row.keyword or "")
        connection.execute(
            search_runs.update()
            .where(search_runs.c.id == row.id)
            .values(keywords_json=json.dumps([keyword], ensure_ascii=False))
        )
    rows = connection.execute(
        sa.select(search_results.c.id, search_runs.c.keyword).join(
            search_runs, search_runs.c.id == search_results.c.search_run_id
        )
    )
    for row in rows:
        keyword = str(row.keyword or "")
        connection.execute(
            search_results.update()
            .where(search_results.c.id == row.id)
            .values(
                matched_keywords_json=json.dumps([keyword], ensure_ascii=False),
                matched_keywords_normalized=normalize_text(keyword),
            )
        )


def downgrade() -> None:
    op.drop_column("search_results", "matched_keywords_normalized")
    op.drop_column("search_results", "matched_keywords_json")
    op.drop_column("search_runs", "keywords_json")
