from __future__ import annotations

from typing import Any

from vk_chat_bot.links import extract_from_sources
from vk_chat_bot.search_service import SearchProcessor
from vk_chat_bot.vk_client import VKSearchResponse


class FakeVK:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.group_ids: list[int] = []
        self.post_ids: list[str] = []

    async def search_groups(self, query: str) -> VKSearchResponse:
        self.queries.append(query)
        if query.startswith("шабашка"):
            return VKSearchResponse(
                items=[
                    {"id": 1, "name": "Шабашка Ростов"},
                    {"id": 2, "name": "Шабашка Москва"},
                ],
                total=1001,
                truncated=True,
            )
        return VKSearchResponse(
            items=[
                {"id": 1, "name": "Шабашка Ростов"},
                {"id": 3, "name": "Ростов — шабашка 24"},
            ],
            total=2,
            truncated=False,
        )

    async def get_groups_by_ids(self, group_ids: list[int]) -> list[dict[str, Any]]:
        self.group_ids = group_ids
        return [
            {
                "id": 1,
                "name": "Шабашка Ростов",
                "screen_name": "shabashka_rostov",
                "description": "Чат: vk.ru/join/from_description",
                "status": "",
                "site": "",
                "links": [],
                "fixed_post": 42,
            },
            {
                "id": 3,
                "name": "Ростов — шабашка 24",
                "screen_name": "rostov_job",
                "description": "",
                "status": "",
                "site": "https://vk.com/join/from_site",
                "links": [],
            },
            {
                "id": 2,
                "name": "Шабашка Москва",
                "screen_name": "shabashka_moscow",
                "description": "",
                "status": "",
                "site": "",
                "links": [],
            },
        ]

    async def get_posts_by_ids(self, post_ids: list[str]) -> list[dict[str, Any]]:
        self.post_ids = post_ids
        return [
            {
                "owner_id": -1,
                "id": 42,
                "text": "Закреп: https://vk.me/join/from_pin",
                "attachments": [
                    {
                        "type": "link",
                        "link": {"url": "https://vk.com/join/from_attachment"},
                    }
                ],
            }
        ]


class MultiKeywordVK:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search_groups(self, query: str) -> VKSearchResponse:
        self.queries.append(query)
        if "услуги" in query.casefold():
            item = {"id": 20, "name": "Услуги Ростов"}
        else:
            item = {"id": 10, "name": "Шабашка Ростов"}
        return VKSearchResponse(items=[item], total=1, truncated=False)

    async def get_groups_by_ids(self, group_ids: list[int]) -> list[dict[str, Any]]:
        payloads = {
            10: {
                "id": 10,
                "name": "Шабашка Ростов",
                "screen_name": "jobs_rostov",
                "description": "",
                "status": "",
                "site": "",
                "links": [],
            },
            20: {
                "id": 20,
                "name": "Услуги Ростов",
                "screen_name": "services_rostov",
                "description": "",
                "status": "",
                "site": "",
                "links": [],
            },
        }
        return [payloads[group_id] for group_id in group_ids]

    async def get_posts_by_ids(self, post_ids: list[str]) -> list[dict[str, Any]]:
        return []


async def test_complete_search_pipeline_and_user_isolation(database) -> None:
    _, sessions, repository = database
    owner = await repository.upsert_user(100, "owner", "Owner")
    outsider = await repository.upsert_user(200, "other", "Other")
    search = await repository.create_search(
        user_id=owner.id,
        keyword="шабашка",
        city="Ростов",
        telegram_chat_id=100,
    )
    fake = FakeVK()
    processor = SearchProcessor(repository=repository, session_factory=sessions, vk=fake)  # type: ignore[arg-type]
    result = await processor.run(search)

    assert fake.queries == ["шабашка Ростов", "Ростов шабашка"]
    assert fake.group_ids == [1, 3]
    assert fake.post_ids == ["-1_42"]
    assert result.found_total == 3
    assert result.matched_total == 2
    assert result.with_chat_total == 2
    assert result.without_chat_total == 0
    assert result.truncated is True

    cards, total = await repository.results_page(
        user_id=owner.id, search_id=search.id, with_chat=True
    )
    assert total == 2
    assert {link.url for card in cards for link in card.links} == {
        "https://vk.ru/join/from_description",
        "https://vk.me/join/from_pin",
        "https://vk.com/join/from_attachment",
        "https://vk.com/join/from_site",
    }
    outsider_cards, outsider_total = await repository.results_page(
        user_id=outsider.id, search_id=search.id, with_chat=True
    )
    assert outsider_cards == []
    assert outsider_total == 0

    filtered, filtered_total = await repository.library_page(
        user_id=owner.id,
        with_chat=True,
        title_query="ШАБАШКА",
    )
    assert filtered_total == 2
    assert len(filtered) == 2


async def test_one_search_can_process_multiple_cities(database) -> None:
    _, sessions, repository = database
    owner = await repository.upsert_user(100, "owner", "Owner")
    search = await repository.create_search(
        user_id=owner.id,
        keyword="шабашка",
        cities=["Ростов", "Москва"],
        telegram_chat_id=100,
    )
    fake = FakeVK()
    processor = SearchProcessor(repository=repository, session_factory=sessions, vk=fake)  # type: ignore[arg-type]

    result = await processor.run(search)

    assert fake.queries == [
        "шабашка Ростов",
        "Ростов шабашка",
        "шабашка Москва",
        "Москва шабашка",
    ]
    assert result.matched_total == 3
    with_chat, with_chat_total = await repository.results_page(
        user_id=owner.id, search_id=search.id, with_chat=True
    )
    without_chat, without_chat_total = await repository.results_page(
        user_id=owner.id, search_id=search.id, with_chat=False
    )
    assert with_chat_total == 2
    assert without_chat_total == 1
    assert {card.vk_id: card.city for card in with_chat + without_chat} == {
        1: "Ростов",
        2: "Москва",
        3: "Ростов",
    }

    filtered, filtered_total = await repository.library_page(
        user_id=owner.id, with_chat=None, city="Москва"
    )
    assert filtered_total == 1
    assert filtered[0].vk_id == 2


async def test_one_search_can_process_multiple_keywords(database) -> None:
    _, sessions, repository = database
    owner = await repository.upsert_user(100, "owner", "Owner")
    search = await repository.create_search(
        user_id=owner.id,
        keywords=["шабашка", "услуги"],
        city="Ростов",
        telegram_chat_id=100,
    )
    fake = MultiKeywordVK()
    processor = SearchProcessor(repository=repository, session_factory=sessions, vk=fake)  # type: ignore[arg-type]

    result = await processor.run(search)

    assert fake.queries == [
        "шабашка Ростов",
        "Ростов шабашка",
        "услуги Ростов",
        "Ростов услуги",
    ]
    assert result.matched_total == 2
    cards, total = await repository.results_page(
        user_id=owner.id, search_id=search.id, with_chat=False
    )
    assert total == 2
    assert {card.vk_id: card.keyword for card in cards} == {
        10: "шабашка",
        20: "услуги",
    }
    filtered, filtered_total = await repository.library_page(
        user_id=owner.id, with_chat=None, keyword="услуги"
    )
    assert filtered_total == 1
    assert filtered[0].vk_id == 20


def test_all_nested_group_and_fixed_post_fields_are_scanned() -> None:
    sources = SearchProcessor._group_sources(
        {
            "name": "Группа",
            "contacts": [{"description": "vk.ru/join/from_contact"}],
            "addresses": {"items": [{"additional_address": "vk.com/join/from_address"}]},
        },
        {
            "text": "без ссылки",
            "copy_history": [{"attachments": [{"link": {"url": "vk.me/join/from_nested"}}]}],
        },
    )
    found = extract_from_sources(sources)

    assert {item.url for item in found} == {
        "https://vk.ru/join/from_contact",
        "https://vk.com/join/from_address",
        "https://vk.me/join/from_nested",
    }
    assert {item.source_type for item in found} == {"group_field", "fixed_post_field"}
