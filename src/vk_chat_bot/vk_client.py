from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GROUP_PROFILE_FIELDS = (
    "id,name,screen_name,is_closed,type,is_admin,admin_level,is_member,is_advertiser,"
    "start_date,finish_date,deactivated,photo_50,photo_100,photo_200,photo_200_orig,"
    "photo_400,photo_400_orig,photo_max,photo_max_orig,est_date,public_date_label,"
    "photo_max_size,is_video_live_notifications_blocked,video_live,market,member_status,"
    "is_adult,is_hidden_from_feed,is_favorite,is_subscribed,city,verified,description,"
    "wiki_page,members_count,members_count_text,requests_count,video_live_level,"
    "video_live_count,clips_count,textlives_count,counters,cover,can_post,can_suggest,"
    "can_upload_story,can_upload_doc,can_upload_video,can_upload_clip,can_see_all_posts,"
    "can_create_topic,activity,fixed_post,has_photo,crop_photo,status,status_audio,"
    "main_album_id,links,contacts,wall,site,main_section,secondary_section,trending,"
    "can_message,is_messages_blocked,can_send_notify,online_status,invited_by,age_limits,"
    "ban_info,has_market_app,using_vkpay_market_app,has_group_channel,addresses,messages,"
    "business_rating,is_subscribed_podcasts,can_subscribe_podcasts,can_subscribe_posts,"
    "live_covers,stories_archive_count,has_unseen_stories,category,category0,category1,"
    "rating,is_market_market_link_attachment_enabled,"
    "is_market_message_to_bc_attachment_enabled,unread_count,videos_count"
)


class VKError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class VKAuthenticationError(VKError):
    pass


class VKCaptchaError(VKError):
    pass


@dataclass(slots=True)
class VKSearchResponse:
    items: list[dict[str, Any]]
    total: int
    truncated: bool


def batched[T](values: Iterable[T], size: int) -> Iterable[list[T]]:
    batch: list[T] = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


class VKClient:
    API_URL = "https://api.vk.com/method"

    def __init__(
        self,
        *,
        access_token: str,
        api_version: str,
        requests_per_second: float = 3.0,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = access_token
        self._version = api_version
        self._interval = 1.0 / requests_per_second
        self._last_request = 0.0
        self._rate_lock = asyncio.Lock()
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _wait_rate_limit(self) -> None:
        async with self._rate_lock:
            delay = self._interval - (time.monotonic() - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()

    async def call(self, method: str, **parameters: Any) -> Any:
        payload = {
            **parameters,
            "access_token": self._token,
            "v": self._version,
        }
        delay = 0.6
        for attempt in range(6):
            await self._wait_rate_limit()
            try:
                response = await self._client.post(f"{self.API_URL}/{method}", data=payload)
                response.raise_for_status()
                data = response.json()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                if attempt == 5:
                    raise VKError("VK временно недоступен по сети.") from exc
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8)
                continue

            error = data.get("error")
            if not error:
                return data.get("response")
            code = int(error.get("error_code", 0))
            message = str(error.get("error_msg", "VK API error"))
            if code == 5:
                raise VKAuthenticationError("VK отклонил access token.", code=code)
            if code == 14:
                raise VKCaptchaError(
                    "VK потребовал CAPTCHA; автоматический обход отключён.", code=code
                )
            if code in {1, 6, 9, 10, 29} and attempt < 5:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 8)
                continue
            raise VKError(f"VK API: {message}", code=code)
        raise VKError("VK API не ответил после повторных попыток.")

    async def search_groups(self, query: str) -> VKSearchResponse:
        response = await self.call("groups.search", q=query, count=1000, sort=0)
        if not isinstance(response, dict):
            raise VKError("VK вернул неожиданный формат поиска.")
        items = [item for item in response.get("items", []) if isinstance(item, dict)]
        total = int(response.get("count", len(items)))
        return VKSearchResponse(
            items=items, total=total, truncated=total > len(items) or len(items) >= 1000
        )

    async def get_groups_by_ids(self, group_ids: list[int]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for chunk in batched(group_ids, 500):
            try:
                response = await self.call(
                    "groups.getById",
                    group_ids=",".join(str(value) for value in chunk),
                    fields=GROUP_PROFILE_FIELDS,
                )
            except VKError as exc:
                if isinstance(exc, (VKAuthenticationError, VKCaptchaError)):
                    raise
                logger.warning("Skipping VK group batch after API error: code=%s", exc.code)
                continue
            if isinstance(response, dict):
                values = response.get("groups", response.get("items", []))
            else:
                values = response
            if isinstance(values, list):
                result.extend(item for item in values if isinstance(item, dict))
        return result

    async def get_posts_by_ids(self, post_ids: list[str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for chunk in batched(post_ids, 100):
            try:
                response = await self.call("wall.getById", posts=",".join(chunk), extended=0)
            except VKError as exc:
                if isinstance(exc, (VKAuthenticationError, VKCaptchaError)):
                    raise
                logger.warning("Skipping VK fixed-post batch after API error: code=%s", exc.code)
                continue
            if isinstance(response, dict):
                values = response.get("items", [])
            else:
                values = response
            if isinstance(values, list):
                result.extend(item for item in values if isinstance(item, dict))
        return result
