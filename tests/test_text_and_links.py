from __future__ import annotations

import pytest

from vk_chat_bot.links import extract_from_sources, extract_join_urls, normalize_join_url
from vk_chat_bot.text import normalize_text, title_matches, validate_search_term


def test_normalize_russian_text_and_separators() -> None:
    assert normalize_text("  РОСТОВ—на-Дону, Ёлка ") == "ростов на дону елка"


@pytest.mark.parametrize(
    ("title", "keyword", "city", "expected"),
    [
        ("Шабашка — Ростов-на-Дону", "шабашка", "Ростов на Дону", True),
        ("Ростов | Работа и подработка", "работа", "ростов", True),
        ("Работа в Москве", "работа", "Ростов", False),
        ("Подработка Ростов", "шабашка", "Ростов", False),
    ],
)
def test_title_matching(title: str, keyword: str, city: str, expected: bool) -> None:
    assert title_matches(title, keyword, city) is expected


def test_validate_search_term() -> None:
    assert validate_search_term("  Ростов  на  Дону ", maximum=128) == "Ростов на Дону"
    with pytest.raises(ValueError):
        validate_search_term("!")


def test_extract_all_supported_vk_join_domains() -> None:
    text = (
        "vk.com/join/Ab_Cd, https://VK.RU/join/second?act=join; "
        "и https://vk.me/join/third). Повтор: https://vk.com/join/Ab_Cd"
    )
    assert extract_join_urls(text) == [
        "https://vk.com/join/Ab_Cd",
        "https://vk.ru/join/second?act=join",
        "https://vk.me/join/third",
    ]


@pytest.mark.parametrize(
    "value",
    [
        "https://example.org/join/value",
        "https://vk.com/not-join/value",
        "https://vk.com/join/",
        "https://vk.com:8080/join/value",
    ],
)
def test_reject_non_invitation_urls(value: str) -> None:
    assert normalize_join_url(value) is None


def test_preserve_same_link_from_multiple_sources() -> None:
    found = extract_from_sources(
        [
            ("description", "", "vk.ru/join/shared"),
            ("fixed_post", "42", "https://vk.ru/join/shared"),
        ]
    )
    assert [(item.url, item.source_type) for item in found] == [
        ("https://vk.ru/join/shared", "description"),
        ("https://vk.ru/join/shared", "fixed_post"),
    ]
