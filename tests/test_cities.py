from __future__ import annotations

from vk_chat_bot.cities import load_city_records, seed_cities


def test_bundled_city_catalog_is_large_and_unique() -> None:
    records = load_city_records()
    names = [str(item["name"]).casefold().replace("ё", "е") for item in records]
    assert len(records) > 1000
    assert len(names) == len(set(names))
    assert "москва" in names
    assert "ростов-на-дону" in names


async def test_city_repository_search_and_random(database) -> None:
    _, _, repository = database
    count = await seed_cities(repository)
    assert count > 1000
    assert await seed_cities(repository) == count
    matches = await repository.search_cities("ростов")
    assert any(city.name == "Ростов-на-Дону" for city in matches)
    assert await repository.random_city() is not None


async def test_random_city_batches_do_not_repeat_for_a_user(database) -> None:
    _, _, repository = database
    await seed_cities(repository)
    user = await repository.upsert_user(100, "owner", "Owner")

    first = await repository.random_cities_for_user(user.id, 20)
    second = await repository.random_cities_for_user(user.id, 20)

    assert len(first) == 20
    assert len(second) == 20
    assert {city.id for city in first}.isdisjoint(city.id for city in second)
    total, remaining = await repository.random_city_stats(user.id)
    assert remaining == total - 40
