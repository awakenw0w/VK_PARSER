from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vk_chat_bot.models import (
    ChatLink,
    City,
    Community,
    Keyword,
    LinkSource,
    RandomCityDraw,
    SearchResult,
    SearchResultLink,
    SearchRun,
    SearchStatus,
    User,
)
from vk_chat_bot.text import normalize_text

ACTIVE_STATUSES = (SearchStatus.QUEUED.value, SearchStatus.RUNNING.value)


@dataclass(slots=True)
class ResultLinkView:
    url: str
    sources: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResultCard:
    result_id: int
    community_id: int
    vk_id: int
    name: str
    url: str
    keyword: str
    city: str
    discovered_at: datetime
    is_closed: int
    links: list[ResultLinkView] = field(default_factory=list)


SOURCE_LABELS = {
    "description": "описание",
    "status": "статус",
    "site": "сайт",
    "links": "блок ссылок",
    "fixed_post": "закреп",
    "fixed_post_attachment": "ссылка в закрепе",
    "group_field": "поле группы",
    "fixed_post_field": "поле закрепа",
}


class Repository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def upsert_user(
        self, telegram_id: int, username: str | None, first_name: str | None
    ) -> User:
        async with self._sessions() as session, session.begin():
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                user = User(telegram_id=telegram_id, username=username, first_name=first_name)
                session.add(user)
                await session.flush()
            else:
                user.username = username
                user.first_name = first_name
            return user

    async def get_user(self, telegram_id: int) -> User | None:
        async with self._sessions() as session:
            return await session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def set_user_access(self, telegram_id: int, *, allowed: bool) -> User:
        async with self._sessions() as session, session.begin():
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                user = User(
                    telegram_id=telegram_id,
                    username=None,
                    first_name=None,
                    has_access=allowed,
                )
                session.add(user)
                await session.flush()
            else:
                user.has_access = allowed
            return user

    async def list_users_with_access(self, *, limit: int = 100) -> list[User]:
        async with self._sessions() as session:
            return list(
                await session.scalars(
                    select(User)
                    .where(User.has_access.is_(True))
                    .order_by(User.telegram_id)
                    .limit(limit)
                )
            )

    async def seed_global_keywords(self, values: list[str]) -> None:
        async with self._sessions() as session, session.begin():
            existing = set(
                await session.scalars(
                    select(Keyword.normalized_text).where(Keyword.scope == "global")
                )
            )
            for value in values:
                normalized = normalize_text(value)
                if normalized and normalized not in existing:
                    session.add(
                        Keyword(
                            owner_user_id=None,
                            scope="global",
                            text=value,
                            normalized_text=normalized,
                        )
                    )
                    existing.add(normalized)

    async def list_keywords(self, user_id: int, *, include_disabled: bool = False) -> list[Keyword]:
        async with self._sessions() as session:
            statement = select(Keyword).where(
                or_(Keyword.scope == "global", Keyword.scope == f"user:{user_id}")
            )
            if not include_disabled:
                statement = statement.where(Keyword.enabled.is_(True))
            statement = statement.order_by(Keyword.scope, Keyword.normalized_text)
            return list(await session.scalars(statement))

    async def list_global_keywords(self) -> list[Keyword]:
        async with self._sessions() as session:
            return list(
                await session.scalars(
                    select(Keyword)
                    .where(Keyword.scope == "global")
                    .order_by(Keyword.normalized_text)
                )
            )

    async def get_keyword_for_user(self, keyword_id: int, user_id: int) -> Keyword | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(Keyword).where(
                    Keyword.id == keyword_id,
                    or_(Keyword.scope == "global", Keyword.scope == f"user:{user_id}"),
                    Keyword.enabled.is_(True),
                )
            )

    async def add_keyword(self, text: str, *, user_id: int | None = None) -> Keyword:
        normalized = normalize_text(text)
        scope = "global" if user_id is None else f"user:{user_id}"
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(Keyword).where(Keyword.scope == scope, Keyword.normalized_text == normalized)
            )
            if existing:
                existing.text = text
                existing.enabled = True
                return existing
            keyword = Keyword(
                owner_user_id=user_id,
                scope=scope,
                text=text,
                normalized_text=normalized,
            )
            session.add(keyword)
            await session.flush()
            return keyword

    async def delete_keyword(
        self, keyword_id: int, *, user_id: int | None = None, global_only: bool = False
    ) -> bool:
        scope = "global" if global_only else f"user:{user_id}"
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                delete(Keyword).where(Keyword.id == keyword_id, Keyword.scope == scope)
            )
            return bool(result.rowcount)

    async def replace_cities(self, records: list[dict[str, str | None]]) -> None:
        async with self._sessions() as session, session.begin():
            existing = set(await session.scalars(select(City.normalized_name)))
            for record in records:
                name = str(record["name"])
                normalized = normalize_text(name)
                if not normalized or normalized in existing:
                    continue
                session.add(
                    City(
                        name=name,
                        normalized_name=normalized,
                        first_letter=normalized[0].upper(),
                        fias_id=record.get("fias_id"),
                    )
                )
                existing.add(normalized)

    async def city_count(self) -> int:
        async with self._sessions() as session:
            return int(await session.scalar(select(func.count(City.id))) or 0)

    async def search_cities(self, query: str, *, limit: int = 10) -> list[City]:
        normalized = normalize_text(query)
        async with self._sessions() as session:
            return list(
                await session.scalars(
                    select(City)
                    .where(City.normalized_name.contains(normalized))
                    .order_by(
                        (City.normalized_name == normalized).desc(),
                        City.normalized_name.startswith(normalized).desc(),
                        City.normalized_name,
                    )
                    .limit(limit)
                )
            )

    async def list_cities_by_letter(
        self, letter: str, *, offset: int = 0, limit: int = 20
    ) -> tuple[list[City], int]:
        normalized_letter = normalize_text(letter)[:1].upper()
        async with self._sessions() as session:
            condition = City.first_letter == normalized_letter
            total = int(await session.scalar(select(func.count(City.id)).where(condition)) or 0)
            cities = list(
                await session.scalars(
                    select(City)
                    .where(condition)
                    .order_by(City.normalized_name)
                    .offset(offset)
                    .limit(limit)
                )
            )
            return cities, total

    async def random_city(self) -> City | None:
        async with self._sessions() as session:
            return await session.scalar(select(City).order_by(func.random()).limit(1))

    async def random_city_stats(self, user_id: int) -> tuple[int, int]:
        async with self._sessions() as session:
            total = int(await session.scalar(select(func.count(City.id))) or 0)
            drawn = int(
                await session.scalar(
                    select(func.count(RandomCityDraw.id)).where(
                        RandomCityDraw.user_id == user_id
                    )
                )
                or 0
            )
            return total, max(0, total - drawn)

    async def random_cities_for_user(
        self,
        user_id: int,
        count: int,
        *,
        exclude_city_ids: set[int] | None = None,
    ) -> list[City]:
        """Draw unique cities and start a new cycle only after exhaustion."""
        excluded = exclude_city_ids or set()
        async with self._sessions() as session, session.begin():
            statement = select(City).order_by(func.random())
            if excluded:
                statement = statement.where(City.id.not_in(excluded))
            available = list(await session.scalars(statement))
            target = min(max(0, count), len(available))
            if target == 0:
                return []

            drawn_ids = set(
                await session.scalars(
                    select(RandomCityDraw.city_id).where(RandomCityDraw.user_id == user_id)
                )
            )
            selected = [city for city in available if city.id not in drawn_ids][:target]
            draws_for_new_cycle: list[City]
            if len(selected) < target:
                await session.execute(
                    delete(RandomCityDraw).where(RandomCityDraw.user_id == user_id)
                )
                selected_ids = {city.id for city in selected}
                draws_for_new_cycle = [
                    city for city in available if city.id not in selected_ids
                ][: target - len(selected)]
                selected.extend(draws_for_new_cycle)
            else:
                draws_for_new_cycle = selected
            session.add_all(
                RandomCityDraw(user_id=user_id, city_id=city.id)
                for city in draws_for_new_cycle
            )
            return selected

    async def random_city_for_user(self, user_id: int) -> City | None:
        cities = await self.random_cities_for_user(user_id, 1)
        return cities[0] if cities else None

    async def get_city(self, city_id: int) -> City | None:
        async with self._sessions() as session:
            return await session.get(City, city_id)

    async def recent_cities(self, user_id: int, *, limit: int = 5) -> list[str]:
        async with self._sessions() as session:
            searches = list(
                await session.scalars(
                    select(SearchRun)
                .where(SearchRun.user_id == user_id)
                    .order_by(SearchRun.created_at.desc())
                    .limit(50)
                )
            )
            result: list[str] = []
            normalized_seen: set[str] = set()
            for search in searches:
                for city in search.cities:
                    normalized = normalize_text(city)
                    if normalized and normalized not in normalized_seen:
                        normalized_seen.add(normalized)
                        result.append(city)
                        if len(result) == limit:
                            return result
            return result

    async def create_search(
        self,
        *,
        user_id: int,
        keyword: str | None = None,
        keywords: list[str] | None = None,
        city: str | None = None,
        cities: list[str] | None = None,
        telegram_chat_id: int,
        telegram_message_id: int | None = None,
    ) -> SearchRun:
        async with self._sessions() as session, session.begin():
            active = await session.scalar(
                select(SearchRun.id).where(
                    SearchRun.user_id == user_id, SearchRun.status.in_(ACTIVE_STATUSES)
                )
            )
            if active is not None:
                raise ValueError("У вас уже есть активный поиск.")
            keyword_values: list[str] = []
            normalized_keywords: set[str] = set()
            for value in keywords or ([keyword] if keyword else []):
                cleaned = " ".join(value.split()).strip()
                normalized = normalize_text(cleaned)
                if normalized and normalized not in normalized_keywords:
                    normalized_keywords.add(normalized)
                    keyword_values.append(cleaned)
            if not keyword_values:
                raise ValueError("Выберите хотя бы одно ключевое слово.")

            city_values: list[str] = []
            normalized_seen: set[str] = set()
            for value in cities or ([city] if city else []):
                cleaned = " ".join(value.split()).strip()
                normalized = normalize_text(cleaned)
                if normalized and normalized not in normalized_seen:
                    normalized_seen.add(normalized)
                    city_values.append(cleaned)
            if not city_values:
                raise ValueError("Выберите хотя бы один город.")
            keyword_label = (
                keyword_values[0]
                if len(keyword_values) == 1
                else f"{len(keyword_values)} ключей"
            )
            city_label = city_values[0] if len(city_values) == 1 else f"{len(city_values)} городов"
            search = SearchRun(
                user_id=user_id,
                keyword=keyword_label,
                keyword_normalized=normalize_text(keyword_label),
                keywords_json=json.dumps(keyword_values, ensure_ascii=False),
                city=city_label,
                city_normalized=normalize_text(city_label),
                cities_json=json.dumps(city_values, ensure_ascii=False),
                telegram_chat_id=telegram_chat_id,
                telegram_message_id=telegram_message_id,
            )
            session.add(search)
            await session.flush()
            return search

    async def set_search_message(self, search_id: int, message_id: int) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(SearchRun)
                .where(SearchRun.id == search_id)
                .values(telegram_message_id=message_id)
            )

    async def get_search(self, search_id: int) -> SearchRun | None:
        async with self._sessions() as session:
            return await session.get(SearchRun, search_id)

    async def active_search_for_user(self, user_id: int) -> SearchRun | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(SearchRun)
                .where(SearchRun.user_id == user_id, SearchRun.status.in_(ACTIVE_STATUSES))
                .order_by(SearchRun.created_at.desc())
            )

    async def request_cancel(self, user_id: int) -> SearchRun | None:
        async with self._sessions() as session, session.begin():
            search = await session.scalar(
                select(SearchRun)
                .where(SearchRun.user_id == user_id, SearchRun.status.in_(ACTIVE_STATUSES))
                .order_by(SearchRun.created_at.desc())
            )
            if search:
                search.cancel_requested = True
                if search.status == SearchStatus.QUEUED.value:
                    search.status = SearchStatus.CANCELLED.value
                    search.completed_at = datetime.now(UTC)
            return search

    async def requeue_interrupted(self) -> int:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(SearchRun)
                .where(SearchRun.status == SearchStatus.RUNNING.value)
                .values(
                    status=SearchStatus.QUEUED.value,
                    progress_stage="queued",
                    progress_current=0,
                    progress_total=0,
                    started_at=None,
                )
            )
            return int(result.rowcount or 0)

    async def claim_next_search(self) -> SearchRun | None:
        async with self._sessions() as session, session.begin():
            search = await session.scalar(
                select(SearchRun)
                .where(
                    SearchRun.status == SearchStatus.QUEUED.value,
                    SearchRun.cancel_requested.is_(False),
                )
                .order_by(SearchRun.created_at)
                .limit(1)
            )
            if search:
                search.status = SearchStatus.RUNNING.value
                search.progress_stage = "searching"
                search.started_at = datetime.now(UTC)
                await session.flush()
            return search

    async def update_search(self, search_id: int, **values: Any) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(SearchRun).where(SearchRun.id == search_id).values(**values)
            )

    async def is_cancel_requested(self, search_id: int) -> bool:
        async with self._sessions() as session:
            return bool(
                await session.scalar(
                    select(SearchRun.cancel_requested).where(SearchRun.id == search_id)
                )
            )

    async def list_history(
        self, user_id: int, *, offset: int = 0, limit: int = 10
    ) -> tuple[list[SearchRun], int]:
        async with self._sessions() as session:
            total = int(
                await session.scalar(
                    select(func.count(SearchRun.id)).where(SearchRun.user_id == user_id)
                )
                or 0
            )
            items = list(
                await session.scalars(
                    select(SearchRun)
                    .where(SearchRun.user_id == user_id)
                    .order_by(SearchRun.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            return items, total

    async def _hydrate_cards(
        self, session: AsyncSession, rows: list[tuple[SearchResult, Community, SearchRun]]
    ) -> list[ResultCard]:
        if not rows:
            return []
        result_ids = [result.id for result, _, _ in rows]
        source_rows = await session.execute(
            select(
                SearchResultLink.search_result_id,
                ChatLink.url,
                LinkSource.source_type,
                LinkSource.source_ref,
            )
            .join(ChatLink, ChatLink.id == SearchResultLink.chat_link_id)
            .outerjoin(LinkSource, LinkSource.chat_link_id == ChatLink.id)
            .where(SearchResultLink.search_result_id.in_(result_ids))
            .order_by(ChatLink.id, LinkSource.id)
        )
        links: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for result_id, url, source_type, source_ref in source_rows:
            if source_type:
                label = SOURCE_LABELS.get(source_type, source_type)
                if source_ref and source_type in {"group_field", "fixed_post_field"}:
                    label = f"{label}: {source_ref}"
                if label not in links[result_id][url]:
                    links[result_id][url].append(label)
            else:
                links[result_id][url]

        cards: list[ResultCard] = []
        for result, community, search in rows:
            cards.append(
                ResultCard(
                    result_id=result.id,
                    community_id=community.id,
                    vk_id=community.vk_id,
                    name=community.name,
                    url=community.url,
                    keyword=", ".join(result.matched_keywords or search.keywords),
                    city=", ".join(result.matched_cities or search.cities),
                    discovered_at=result.discovered_at,
                    is_closed=community.is_closed,
                    links=[
                        ResultLinkView(url=url, sources=sources)
                        for url, sources in links[result.id].items()
                    ],
                )
            )
        return cards

    async def results_page(
        self,
        *,
        user_id: int,
        search_id: int,
        with_chat: bool,
        offset: int = 0,
        limit: int = 10,
    ) -> tuple[list[ResultCard], int]:
        async with self._sessions() as session:
            ownership = (SearchRun.id == search_id, SearchRun.user_id == user_id)
            total = int(
                await session.scalar(
                    select(func.count(SearchResult.id))
                    .join(SearchRun, SearchRun.id == SearchResult.search_run_id)
                    .where(*ownership, SearchResult.has_chat.is_(with_chat))
                )
                or 0
            )
            rows = list(
                (
                    await session.execute(
                        select(SearchResult, Community, SearchRun)
                        .join(SearchRun, SearchRun.id == SearchResult.search_run_id)
                        .join(Community, Community.id == SearchResult.community_id)
                        .where(*ownership, SearchResult.has_chat.is_(with_chat))
                        .order_by(SearchResult.position)
                        .offset(offset)
                        .limit(limit)
                    )
                ).tuples()
            )
            return await self._hydrate_cards(session, rows), total

    async def library_page(
        self,
        *,
        user_id: int,
        with_chat: bool | None,
        title_query: str = "",
        keyword: str = "",
        city: str = "",
        offset: int = 0,
        limit: int = 10,
    ) -> tuple[list[ResultCard], int]:
        conditions = [SearchRun.user_id == user_id]
        if with_chat is not None:
            conditions.append(SearchResult.has_chat.is_(with_chat))
        if title_query:
            conditions.append(Community.normalized_name.contains(normalize_text(title_query)))
        if keyword:
            conditions.append(
                SearchResult.matched_keywords_normalized.contains(normalize_text(keyword))
            )
        if city:
            conditions.append(
                SearchResult.matched_cities_normalized.contains(normalize_text(city))
            )

        async with self._sessions() as session:
            latest_ids = (
                select(func.max(SearchResult.id).label("result_id"))
                .join(SearchRun, SearchRun.id == SearchResult.search_run_id)
                .join(Community, Community.id == SearchResult.community_id)
                .where(*conditions)
                .group_by(SearchResult.community_id)
                .subquery()
            )
            total = int(await session.scalar(select(func.count()).select_from(latest_ids)) or 0)
            rows = list(
                (
                    await session.execute(
                        select(SearchResult, Community, SearchRun)
                        .join(latest_ids, latest_ids.c.result_id == SearchResult.id)
                        .join(SearchRun, SearchRun.id == SearchResult.search_run_id)
                        .join(Community, Community.id == SearchResult.community_id)
                        .order_by(SearchResult.discovered_at.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                ).tuples()
            )
            return await self._hydrate_cards(session, rows), total
