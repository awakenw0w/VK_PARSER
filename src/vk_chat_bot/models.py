from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SearchStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    has_access: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Keyword(Base):
    __tablename__ = "keywords"
    __table_args__ = (UniqueConstraint("scope", "normalized_text", name="uq_keyword_scope"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(String(64))
    normalized_text: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class City(Base):
    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    first_letter: Mapped[str] = mapped_column(String(1), index=True)
    fias_id: Mapped[str | None] = mapped_column(String(36))


class RandomCityDraw(Base):
    __tablename__ = "random_city_draws"
    __table_args__ = (
        UniqueConstraint("user_id", "city_id", name="uq_random_city_draw_user_city"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    city_id: Mapped[int] = mapped_column(
        ForeignKey("cities.id", ondelete="CASCADE"), index=True
    )
    drawn_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Community(Base):
    __tablename__ = "communities"

    id: Mapped[int] = mapped_column(primary_key=True)
    vk_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255), index=True)
    screen_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="")
    site: Mapped[str] = mapped_column(Text, default="")
    is_closed: Mapped[int] = mapped_column(Integer, default=0)
    deactivated: Mapped[str | None] = mapped_column(String(32))
    fixed_post_id: Mapped[int | None] = mapped_column(BigInteger)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    chat_links: Mapped[list[ChatLink]] = relationship(
        back_populates="community", cascade="all, delete-orphan"
    )

    @property
    def url(self) -> str:
        return f"https://vk.com/{self.screen_name or f'club{self.vk_id}'}"


class ChatLink(Base):
    __tablename__ = "chat_links"
    __table_args__ = (UniqueConstraint("community_id", "url", name="uq_community_chat_link"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    community_id: Mapped[int] = mapped_column(
        ForeignKey("communities.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    community: Mapped[Community] = relationship(back_populates="chat_links")
    sources: Mapped[list[LinkSource]] = relationship(
        back_populates="chat_link", cascade="all, delete-orphan"
    )


class LinkSource(Base):
    __tablename__ = "link_sources"
    __table_args__ = (
        UniqueConstraint("chat_link_id", "source_type", "source_ref", name="uq_link_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_link_id: Mapped[int] = mapped_column(
        ForeignKey("chat_links.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(String(255), default="")

    chat_link: Mapped[ChatLink] = relationship(back_populates="sources")


class SearchRun(Base):
    __tablename__ = "search_runs"
    __table_args__ = (
        Index("ix_search_user_status", "user_id", "status"),
        Index("ix_search_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    keyword: Mapped[str] = mapped_column(String(64))
    keyword_normalized: Mapped[str] = mapped_column(String(64))
    keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    city: Mapped[str] = mapped_column(String(128))
    city_normalized: Mapped[str] = mapped_column(String(128))
    cities_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(16), default=SearchStatus.QUEUED, index=True)
    progress_stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    found_total: Mapped[int] = mapped_column(Integer, default=0)
    matched_total: Mapped[int] = mapped_column(Integer, default=0)
    with_chat_total: Mapped[int] = mapped_column(Integer, default=0)
    without_chat_total: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def keywords(self) -> list[str]:
        try:
            values = json.loads(self.keywords_json)
        except (TypeError, json.JSONDecodeError):
            values = []
        result = [str(value) for value in values if str(value).strip()]
        return result or ([self.keyword] if self.keyword else [])

    @property
    def keyword_label(self) -> str:
        keywords = self.keywords
        if len(keywords) == 1:
            return keywords[0]
        return f"{len(keywords)} ключей"

    @property
    def cities(self) -> list[str]:
        try:
            values = json.loads(self.cities_json)
        except (TypeError, json.JSONDecodeError):
            values = []
        result = [str(value) for value in values if str(value).strip()]
        return result or ([self.city] if self.city else [])

    @property
    def city_label(self) -> str:
        cities = self.cities
        if len(cities) == 1:
            return cities[0]
        return f"{len(cities)} городов"


class SearchResult(Base):
    __tablename__ = "search_results"
    __table_args__ = (
        UniqueConstraint("search_run_id", "community_id", name="uq_search_community"),
        Index("ix_result_search_chat_pos", "search_run_id", "has_chat", "position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    search_run_id: Mapped[int] = mapped_column(
        ForeignKey("search_runs.id", ondelete="CASCADE"), index=True
    )
    community_id: Mapped[int] = mapped_column(
        ForeignKey("communities.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    has_chat: Mapped[bool] = mapped_column(Boolean, default=False)
    matched_keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    matched_keywords_normalized: Mapped[str] = mapped_column(Text, default="")
    matched_cities_json: Mapped[str] = mapped_column(Text, default="[]")
    matched_cities_normalized: Mapped[str] = mapped_column(Text, default="")
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def matched_keywords(self) -> list[str]:
        try:
            values = json.loads(self.matched_keywords_json)
        except (TypeError, json.JSONDecodeError):
            return []
        return [str(value) for value in values if str(value).strip()]

    @property
    def matched_cities(self) -> list[str]:
        try:
            values = json.loads(self.matched_cities_json)
        except (TypeError, json.JSONDecodeError):
            return []
        return [str(value) for value in values if str(value).strip()]


class SearchResultLink(Base):
    __tablename__ = "search_result_links"
    __table_args__ = (
        UniqueConstraint("search_result_id", "chat_link_id", name="uq_result_chat_link"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    search_result_id: Mapped[int] = mapped_column(
        ForeignKey("search_results.id", ondelete="CASCADE"), index=True
    )
    chat_link_id: Mapped[int] = mapped_column(
        ForeignKey("chat_links.id", ondelete="CASCADE"), index=True
    )
