from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vk_chat_bot.models import SearchStatus


async def test_keywords_are_scoped_per_user(database) -> None:
    _, _, repository = database
    first = await repository.upsert_user(100, "one", "One")
    second = await repository.upsert_user(200, "two", "Two")
    await repository.seed_global_keywords(["работа"])
    await repository.add_keyword("монтаж", user_id=first.id)

    first_words = {item.text for item in await repository.list_keywords(first.id)}
    second_words = {item.text for item in await repository.list_keywords(second.id)}
    assert first_words == {"работа", "монтаж"}
    assert second_words == {"работа"}


async def test_one_active_search_and_recovery(database) -> None:
    _, _, repository = database
    user = await repository.upsert_user(100, None, "One")
    first = await repository.create_search(
        user_id=user.id,
        keyword="работа",
        city="Ростов",
        telegram_chat_id=100,
    )
    with pytest.raises(ValueError, match="активный поиск"):
        await repository.create_search(
            user_id=user.id,
            keyword="шабашка",
            city="Москва",
            telegram_chat_id=100,
        )

    cancelled = await repository.request_cancel(user.id)
    assert cancelled is not None
    stored = await repository.get_search(first.id)
    assert stored and stored.status == SearchStatus.CANCELLED.value

    second = await repository.create_search(
        user_id=user.id,
        keyword="шабашка",
        city="Москва",
        telegram_chat_id=100,
    )
    await repository.update_search(
        second.id,
        status=SearchStatus.RUNNING.value,
        started_at=datetime.now(UTC),
    )
    assert await repository.requeue_interrupted() == 1
    recovered = await repository.get_search(second.id)
    assert recovered and recovered.status == SearchStatus.QUEUED.value
