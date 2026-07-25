from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vk_chat_bot.links import FoundLink, extract_from_sources
from vk_chat_bot.models import (
    ChatLink,
    Community,
    LinkSource,
    SearchResult,
    SearchResultLink,
    SearchRun,
    SearchStatus,
)
from vk_chat_bot.repositories import Repository
from vk_chat_bot.text import normalize_text, title_matches
from vk_chat_bot.vk_client import VKClient

ProgressCallback = Callable[[SearchRun], Awaitable[None]]


class SearchCancelled(RuntimeError):
    pass


class SearchProcessor:
    def __init__(
        self,
        *,
        repository: Repository,
        session_factory: async_sessionmaker[AsyncSession],
        vk: VKClient,
    ) -> None:
        self._repository = repository
        self._sessions = session_factory
        self._vk = vk

    async def _check_cancelled(self, search_id: int) -> None:
        if await self._repository.is_cancel_requested(search_id):
            raise SearchCancelled

    async def _progress(
        self,
        search_id: int,
        callback: ProgressCallback | None,
        *,
        stage: str,
        current: int = 0,
        total: int = 0,
        **values: Any,
    ) -> None:
        await self._repository.update_search(
            search_id,
            progress_stage=stage,
            progress_current=current,
            progress_total=total,
            **values,
        )
        if callback:
            search = await self._repository.get_search(search_id)
            if search:
                await callback(search)

    @staticmethod
    def _string_sources(
        value: Any, *, source_type: str, path: str = ""
    ) -> list[tuple[str, str, str]]:
        sources: list[tuple[str, str, str]] = []
        if isinstance(value, str):
            if value:
                sources.append((source_type, path, value))
            return sources
        if isinstance(value, dict):
            for key, nested in value.items():
                nested_path = f"{path}.{key}" if path else str(key)
                sources.extend(
                    SearchProcessor._string_sources(
                        nested, source_type=source_type, path=nested_path
                    )
                )
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                nested_path = f"{path}[{index}]" if path else f"[{index}]"
                sources.extend(
                    SearchProcessor._string_sources(
                        nested, source_type=source_type, path=nested_path
                    )
                )
        return sources

    @staticmethod
    def _group_sources(
        group: dict[str, Any], fixed_post: dict[str, Any] | None
    ) -> list[tuple[str, str, str]]:
        sources = SearchProcessor._string_sources(group, source_type="group_field")
        if fixed_post:
            sources.extend(
                SearchProcessor._string_sources(
                    fixed_post, source_type="fixed_post_field"
                )
            )
        return sources

    async def _persist_results(
        self,
        search: SearchRun,
        groups: list[dict[str, Any]],
        links_by_vk_id: dict[int, list[FoundLink]],
        matched_keywords_by_vk_id: dict[int, list[str]],
        matched_cities_by_vk_id: dict[int, list[str]],
    ) -> tuple[int, int]:
        now = datetime.now(UTC)
        with_chat_count = 0
        async with self._sessions() as session, session.begin():
            # Makes a recovered job safe to run again if the previous process
            # committed its snapshot just before it stopped.
            await session.execute(
                delete(SearchResult).where(SearchResult.search_run_id == search.id)
            )
            for position, payload in enumerate(groups):
                vk_id = int(payload["id"])
                community = await session.scalar(select(Community).where(Community.vk_id == vk_id))
                if community is None:
                    name = str(payload.get("name") or vk_id)
                    community = Community(
                        vk_id=vk_id,
                        name=name,
                        normalized_name=normalize_text(name),
                    )
                    session.add(community)
                    await session.flush()
                community.name = str(payload.get("name") or community.name)
                community.normalized_name = normalize_text(community.name)
                community.screen_name = payload.get("screen_name")
                community.description = str(payload.get("description") or "")
                community.status = str(payload.get("status") or "")
                community.site = str(payload.get("site") or "")
                community.is_closed = int(payload.get("is_closed") or 0)
                community.deactivated = payload.get("deactivated")
                community.fixed_post_id = payload.get("fixed_post")
                community.fetched_at = now

                found_links = links_by_vk_id.get(vk_id, [])
                unique_urls = list(dict.fromkeys(item.url for item in found_links))
                matched_keywords = matched_keywords_by_vk_id.get(vk_id, search.keywords)
                matched_cities = matched_cities_by_vk_id.get(vk_id, search.cities)
                result = SearchResult(
                    search_run_id=search.id,
                    community_id=community.id,
                    position=position,
                    has_chat=bool(unique_urls),
                    matched_keywords_json=json.dumps(matched_keywords, ensure_ascii=False),
                    matched_keywords_normalized="\n".join(
                        normalize_text(keyword) for keyword in matched_keywords
                    ),
                    matched_cities_json=json.dumps(matched_cities, ensure_ascii=False),
                    matched_cities_normalized="\n".join(
                        normalize_text(city) for city in matched_cities
                    ),
                )
                session.add(result)
                await session.flush()
                if unique_urls:
                    with_chat_count += 1

                grouped_sources: dict[str, list[FoundLink]] = defaultdict(list)
                for found in found_links:
                    grouped_sources[found.url].append(found)
                for url in unique_urls:
                    chat_link = await session.scalar(
                        select(ChatLink).where(
                            ChatLink.community_id == community.id, ChatLink.url == url
                        )
                    )
                    if chat_link is None:
                        chat_link = ChatLink(community_id=community.id, url=url)
                        session.add(chat_link)
                        await session.flush()
                    chat_link.last_seen_at = now
                    for found in grouped_sources[url]:
                        source = await session.scalar(
                            select(LinkSource).where(
                                LinkSource.chat_link_id == chat_link.id,
                                LinkSource.source_type == found.source_type,
                                LinkSource.source_ref == found.source_ref,
                            )
                        )
                        if source is None:
                            session.add(
                                LinkSource(
                                    chat_link_id=chat_link.id,
                                    source_type=found.source_type,
                                    source_ref=found.source_ref,
                                )
                            )
                    session.add(
                        SearchResultLink(search_result_id=result.id, chat_link_id=chat_link.id)
                    )
        return with_chat_count, len(groups) - with_chat_count

    async def run(
        self, search: SearchRun, progress_callback: ProgressCallback | None = None
    ) -> SearchRun:
        try:
            await self._check_cancelled(search.id)
            search_queries = [
                (keyword, city, query)
                for keyword in search.keywords
                for city in search.cities
                for query in dict.fromkeys(
                    (f"{keyword} {city}", f"{city} {keyword}")
                )
            ]
            await self._progress(
                search.id,
                progress_callback,
                stage="searching",
                current=0,
                total=len(search_queries),
            )

            found_ids: set[int] = set()
            candidates_by_id: dict[int, dict[str, Any]] = {}
            matched_pairs_by_id: dict[int, list[tuple[str, str]]] = defaultdict(list)
            truncated = False
            for index, (keyword, city, query) in enumerate(search_queries, start=1):
                response = await self._vk.search_groups(query)
                truncated = truncated or response.truncated
                for item in response.items:
                    if "id" not in item:
                        continue
                    vk_id = int(item["id"])
                    found_ids.add(vk_id)
                    if (
                        not item.get("deactivated")
                        and title_matches(str(item.get("name") or ""), keyword, city)
                    ):
                        candidates_by_id.setdefault(vk_id, item)
                        pair = (keyword, city)
                        if pair not in matched_pairs_by_id[vk_id]:
                            matched_pairs_by_id[vk_id].append(pair)
                await self._progress(
                    search.id,
                    progress_callback,
                    stage="searching",
                    current=index,
                    total=len(search_queries),
                    found_total=len(found_ids),
                    truncated=truncated,
                )
                await self._check_cancelled(search.id)

            candidates = list(candidates_by_id.values())
            await self._progress(
                search.id,
                progress_callback,
                stage="enriching",
                current=0,
                total=len(candidates),
                found_total=len(found_ids),
                matched_total=len(candidates),
                truncated=truncated,
            )
            await self._check_cancelled(search.id)

            detailed = await self._vk.get_groups_by_ids([int(item["id"]) for item in candidates])
            details_by_id = {int(item["id"]): item for item in detailed if "id" in item}
            ordered_groups: list[dict[str, Any]] = []
            final_matched_keywords: dict[int, list[str]] = {}
            final_matched_cities: dict[int, list[str]] = {}
            for item in candidates:
                vk_id = int(item["id"])
                detail = details_by_id.get(vk_id)
                if detail is None:
                    continue
                matched_pairs = [
                    (keyword, city)
                    for keyword, city in matched_pairs_by_id[vk_id]
                    if title_matches(str(detail.get("name") or ""), keyword, city)
                ]
                if matched_pairs:
                    ordered_groups.append(detail)
                    final_matched_keywords[vk_id] = list(
                        dict.fromkeys(keyword for keyword, _ in matched_pairs)
                    )
                    final_matched_cities[vk_id] = list(
                        dict.fromkeys(city for _, city in matched_pairs)
                    )
            await self._progress(
                search.id,
                progress_callback,
                stage="fixed_posts",
                current=0,
                total=len(ordered_groups),
                matched_total=len(ordered_groups),
            )
            await self._check_cancelled(search.id)

            post_ids = [
                f"-{int(group['id'])}_{int(group['fixed_post'])}"
                for group in ordered_groups
                if group.get("fixed_post")
            ]
            posts = await self._vk.get_posts_by_ids(post_ids) if post_ids else []
            posts_by_owner: dict[int, dict[str, Any]] = {}
            for post in posts:
                owner_id = int(post.get("owner_id") or 0)
                if owner_id < 0:
                    posts_by_owner[-owner_id] = post

            links_by_group: dict[int, list[FoundLink]] = {}
            for index, group in enumerate(ordered_groups, start=1):
                vk_id = int(group["id"])
                links_by_group[vk_id] = extract_from_sources(
                    self._group_sources(group, posts_by_owner.get(vk_id))
                )
                if index % 50 == 0:
                    await self._progress(
                        search.id,
                        progress_callback,
                        stage="extracting",
                        current=index,
                        total=len(ordered_groups),
                    )
                    await self._check_cancelled(search.id)

            await self._check_cancelled(search.id)
            await self._progress(
                search.id,
                progress_callback,
                stage="saving",
                current=0,
                total=len(ordered_groups),
            )
            with_chat, without_chat = await self._persist_results(
                search,
                ordered_groups,
                links_by_group,
                final_matched_keywords,
                final_matched_cities,
            )
            completed_at = datetime.now(UTC)
            await self._repository.update_search(
                search.id,
                status=SearchStatus.COMPLETED.value,
                progress_stage="completed",
                progress_current=len(ordered_groups),
                progress_total=len(ordered_groups),
                found_total=len(found_ids),
                matched_total=len(ordered_groups),
                with_chat_total=with_chat,
                without_chat_total=without_chat,
                truncated=truncated,
                completed_at=completed_at,
            )
        except SearchCancelled:
            await self._repository.update_search(
                search.id,
                status=SearchStatus.CANCELLED.value,
                progress_stage="cancelled",
                completed_at=datetime.now(UTC),
            )
        result = await self._repository.get_search(search.id)
        if result is None:
            raise RuntimeError("Search disappeared from database")
        return result
