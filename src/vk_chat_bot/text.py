from __future__ import annotations

import re
import unicodedata

_SEPARATORS_RE = re.compile(r"[^0-9a-zа-я]+", re.IGNORECASE)


def normalize_text(value: str) -> str:
    """Normalize human input for stable Russian substring matching."""
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return _SEPARATORS_RE.sub(" ", value).strip()


def title_matches(title: str, keyword: str, city: str) -> bool:
    normalized_title = normalize_text(title)
    normalized_keyword = normalize_text(keyword)
    normalized_city = normalize_text(city)
    return bool(
        normalized_keyword
        and normalized_city
        and normalized_keyword in normalized_title
        and normalized_city in normalized_title
    )


def validate_search_term(value: str, *, minimum: int = 2, maximum: int = 64) -> str:
    cleaned = " ".join(value.split()).strip()
    if not minimum <= len(cleaned) <= maximum:
        raise ValueError(f"Значение должно содержать от {minimum} до {maximum} символов.")
    if not normalize_text(cleaned):
        raise ValueError("Значение должно содержать буквы или цифры.")
    return cleaned
