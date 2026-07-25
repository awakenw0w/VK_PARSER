from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from vk_chat_bot.repositories import Repository


def load_city_records() -> list[dict[str, str | None]]:
    resource = files("vk_chat_bot").joinpath("data/cities.json")
    with resource.open("r", encoding="utf-8-sig") as stream:
        payload: Any = json.load(stream)
    if not isinstance(payload, dict) or not isinstance(payload.get("cities"), list):
        raise RuntimeError("Invalid bundled city catalog")
    records: list[dict[str, str | None]] = []
    for item in payload["cities"]:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            records.append({"name": item["name"], "fias_id": item.get("fias_id")})
    return records


async def seed_cities(repository: Repository) -> int:
    await repository.replace_cities(load_city_records())
    return await repository.city_count()
