from __future__ import annotations

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

_JOIN_RE = re.compile(
    r"(?<![\w@])(?P<url>(?:(?:https?://)?(?:www\.)?)?"
    r"(?:vk\.com|vk\.ru|vk\.me)/join/[^\s<>\"']+)",
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}>»”'\""
_ALLOWED_HOSTS = frozenset({"vk.com", "vk.ru", "vk.me"})


@dataclass(frozen=True, slots=True)
class FoundLink:
    url: str
    source_type: str
    source_ref: str = ""


def normalize_join_url(raw_url: str) -> str | None:
    candidate = html.unescape(raw_url).strip().rstrip(_TRAILING)
    if not re.match(r"^https?://", candidate, re.IGNORECASE):
        candidate = f"https://{candidate}"
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    if host not in _ALLOWED_HOSTS or not parsed.path.casefold().startswith("/join/"):
        return None
    if len(parsed.path) <= len("/join/"):
        return None
    netloc = host
    if parsed.port:
        return None
    return urlunsplit(("https", netloc, parsed.path, parsed.query, ""))


def extract_join_urls(value: str | None) -> list[str]:
    if not value:
        return []
    decoded = html.unescape(value)
    result: list[str] = []
    seen: set[str] = set()
    for match in _JOIN_RE.finditer(decoded):
        normalized = normalize_join_url(match.group("url"))
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def extract_from_sources(sources: Iterable[tuple[str, str, str]]) -> list[FoundLink]:
    result: list[FoundLink] = []
    seen: set[tuple[str, str, str]] = set()
    for source_type, source_ref, value in sources:
        for url in extract_join_urls(value):
            key = (url, source_type, source_ref)
            if key not in seen:
                seen.add(key)
                result.append(FoundLink(url, source_type, source_ref))
    return result
