"""Initial application schema.

Revision ID: 0001_initial
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64)),
        sa.Column("first_name", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)
    op.create_table(
        "cities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("normalized_name", sa.String(128), nullable=False),
        sa.Column("first_letter", sa.String(1), nullable=False),
        sa.Column("fias_id", sa.String(36)),
        sa.UniqueConstraint("normalized_name"),
    )
    op.create_index("ix_cities_normalized_name", "cities", ["normalized_name"], unique=True)
    op.create_index("ix_cities_first_letter", "cities", ["first_letter"])
    op.create_table(
        "communities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vk_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("screen_name", sa.String(255)),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("site", sa.Text(), nullable=False),
        sa.Column("is_closed", sa.Integer(), nullable=False),
        sa.Column("deactivated", sa.String(32)),
        sa.Column("fixed_post_id", sa.BigInteger()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("vk_id"),
    )
    op.create_index("ix_communities_vk_id", "communities", ["vk_id"], unique=True)
    op.create_table(
        "keywords",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("text", sa.String(64), nullable=False),
        sa.Column("normalized_text", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope", "normalized_text", name="uq_keyword_scope"),
    )
    op.create_index("ix_keywords_scope", "keywords", ["scope"])
    op.create_table(
        "chat_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "community_id",
            sa.Integer(),
            sa.ForeignKey("communities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("community_id", "url", name="uq_community_chat_link"),
    )
    op.create_index("ix_chat_links_community_id", "chat_links", ["community_id"])
    op.create_table(
        "search_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("keyword", sa.String(64), nullable=False),
        sa.Column("keyword_normalized", sa.String(64), nullable=False),
        sa.Column("city", sa.String(128), nullable=False),
        sa.Column("city_normalized", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("progress_stage", sa.String(64), nullable=False),
        sa.Column("progress_current", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=False),
        sa.Column("found_total", sa.Integer(), nullable=False),
        sa.Column("matched_total", sa.Integer(), nullable=False),
        sa.Column("with_chat_total", sa.Integer(), nullable=False),
        sa.Column("without_chat_total", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("telegram_chat_id", sa.BigInteger()),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_search_runs_user_id", "search_runs", ["user_id"])
    op.create_index("ix_search_runs_status", "search_runs", ["status"])
    op.create_index("ix_search_user_status", "search_runs", ["user_id", "status"])
    op.create_index("ix_search_created", "search_runs", ["created_at"])
    op.create_table(
        "link_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "chat_link_id",
            sa.Integer(),
            sa.ForeignKey("chat_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.UniqueConstraint("chat_link_id", "source_type", "source_ref", name="uq_link_source"),
    )
    op.create_index("ix_link_sources_chat_link_id", "link_sources", ["chat_link_id"])
    op.create_table(
        "search_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "search_run_id",
            sa.Integer(),
            sa.ForeignKey("search_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "community_id",
            sa.Integer(),
            sa.ForeignKey("communities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("has_chat", sa.Boolean(), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("search_run_id", "community_id", name="uq_search_community"),
    )
    op.create_index("ix_search_results_search_run_id", "search_results", ["search_run_id"])
    op.create_index("ix_search_results_community_id", "search_results", ["community_id"])
    op.create_index(
        "ix_result_search_chat_pos", "search_results", ["search_run_id", "has_chat", "position"]
    )
    op.create_table(
        "search_result_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "search_result_id",
            sa.Integer(),
            sa.ForeignKey("search_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chat_link_id",
            sa.Integer(),
            sa.ForeignKey("chat_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("search_result_id", "chat_link_id", name="uq_result_chat_link"),
    )
    op.create_index(
        "ix_search_result_links_search_result_id", "search_result_links", ["search_result_id"]
    )
    op.create_index("ix_search_result_links_chat_link_id", "search_result_links", ["chat_link_id"])


def downgrade() -> None:
    for table in [
        "search_result_links",
        "search_results",
        "link_sources",
        "search_runs",
        "chat_links",
        "keywords",
        "communities",
        "cities",
        "users",
    ]:
        op.drop_table(table)
