from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from vk_chat_bot.vk_client import VKAuthenticationError, VKClient


async def test_vk_client_uses_only_explicit_methods_and_handles_shapes() -> None:
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        called.append(method)
        form = parse_qs(request.content.decode())
        assert form["access_token"] == ["secret"]
        if method == "groups.search":
            return httpx.Response(200, json={"response": {"count": 1001, "items": [{"id": 1}]}})
        if method == "groups.getById":
            fields = set(form["fields"][0].split(","))
            assert {"description", "status", "contacts", "addresses", "messages"} <= fields
            return httpx.Response(200, json={"response": {"groups": [{"id": 1}]}})
        if method == "wall.getById":
            return httpx.Response(200, json={"response": {"items": [{"id": 42}]}})
        raise AssertionError(method)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = VKClient(
        access_token="secret",
        api_version="5.199",
        requests_per_second=1000,
        client=http,
    )
    search = await client.search_groups("работа Ростов")
    groups = await client.get_groups_by_ids([1])
    posts = await client.get_posts_by_ids(["-1_42"])
    await http.aclose()

    assert search.truncated is True
    assert groups == [{"id": 1}]
    assert posts == [{"id": 42}]
    assert called == ["groups.search", "groups.getById", "wall.getById"]
    assert "wall.get" not in called
    assert "wall.search" not in called


async def test_vk_authentication_error_is_fatal() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(
                {"error": {"error_code": 5, "error_msg": "User authorization failed"}}
            ).encode(),
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = VKClient(
        access_token="secret",
        api_version="5.199",
        requests_per_second=1000,
        client=http,
    )
    with pytest.raises(VKAuthenticationError):
        await client.search_groups("test")
    await http.aclose()


async def test_vk_rate_limit_is_retried(monkeypatch) -> None:
    attempts = 0

    async def no_sleep(_: float) -> None:
        return None

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(
                200,
                json={"error": {"error_code": 6, "error_msg": "Too many requests"}},
            )
        return httpx.Response(200, json={"response": {"count": 0, "items": []}})

    monkeypatch.setattr("vk_chat_bot.vk_client.asyncio.sleep", no_sleep)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = VKClient(
        access_token="secret",
        api_version="5.199",
        requests_per_second=1000,
        client=http,
    )
    result = await client.search_groups("test")
    await http.aclose()
    assert result.items == []
    assert attempts == 3
