from __future__ import annotations

import asyncio
import html
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from vk_chat_bot.models import SearchRun, SearchStatus
from vk_chat_bot.repositories import Repository
from vk_chat_bot.search_service import SearchProcessor
from vk_chat_bot.vk_client import VKAuthenticationError, VKCaptchaError, VKError

logger = logging.getLogger(__name__)

PROGRESS_EDIT_INTERVAL = 3.0

STAGE_LABELS = {
    "queued": "Ожидает запуска",
    "searching": "Ищу сообщества",
    "enriching": "Получаю данные сообществ",
    "fixed_posts": "Читаю закреплённые записи",
    "extracting": "Ищу ссылки на беседы",
    "saving": "Сохраняю результат",
}


class SearchWorker:
    def __init__(self, *, repository: Repository, processor: SearchProcessor, bot: Bot) -> None:
        self._repository = repository
        self._processor = processor
        self._bot = bot
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_progress_edit: dict[int, tuple[str, float]] = {}

    async def start(self) -> None:
        await self._repository.requeue_interrupted()
        self._task = asyncio.create_task(self._loop(), name="vk-search-worker")
        self._wake.set()

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    def notify(self) -> None:
        self._wake.set()

    @staticmethod
    def progress_text(search: SearchRun) -> str:
        stage = STAGE_LABELS.get(search.progress_stage, search.progress_stage)
        counter = ""
        if search.progress_total:
            counter = f"\nПрогресс: {search.progress_current}/{search.progress_total}"
        keyword_label = "Ключ" if len(search.keywords) == 1 else "Ключи"
        city_label = "Город" if len(search.cities) == 1 else "Города"
        return (
            f"🔎 <b>{stage}</b>\n"
            f"{keyword_label}: <code>{html.escape(search.keyword_label)}</code>\n"
            f"{city_label}: <code>{html.escape(search.city_label)}</code>{counter}\n"
            f"Найдено VK: {search.found_total}\n"
            f"Подошло по названию: {search.matched_total}"
        )

    async def _edit_progress(self, search: SearchRun) -> None:
        if not search.telegram_chat_id or not search.telegram_message_id:
            return
        now = time.monotonic()
        previous_stage, previous_at = self._last_progress_edit.get(search.id, ("", 0.0))
        stage_finished = bool(
            search.progress_total and search.progress_current >= search.progress_total
        )
        if (
            search.progress_stage == previous_stage
            and now - previous_at < PROGRESS_EDIT_INTERVAL
            and not stage_finished
        ):
            return
        self._last_progress_edit[search.id] = (search.progress_stage, now)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Отменить", callback_data=f"cancel:{search.id}")]
            ]
        )
        # Progress delivery is best-effort. Telegram flood control, a deleted
        # message or a blocked bot must never abort the VK search itself.
        with suppress(TelegramAPIError):
            await self._bot.edit_message_text(
                self.progress_text(search),
                chat_id=search.telegram_chat_id,
                message_id=search.telegram_message_id,
                reply_markup=keyboard,
            )

    async def _notify_finished(self, search: SearchRun) -> None:
        self._last_progress_edit.pop(search.id, None)
        if not search.telegram_chat_id or not search.telegram_message_id:
            return
        if search.status == SearchStatus.COMPLETED.value:
            limit_note = "\n⚠️ VK ограничил полноту выдачи." if search.truncated else ""
            keyword_summary = (
                html.escape(search.keywords[0])
                if len(search.keywords) == 1
                else str(len(search.keywords))
            )
            keyword_label = "Ключ" if len(search.keywords) == 1 else "Ключей"
            city_summary = (
                html.escape(search.cities[0])
                if len(search.cities) == 1
                else str(len(search.cities))
            )
            city_label = "Город" if len(search.cities) == 1 else "Городов"
            text = (
                "✅ <b>Поиск завершён</b>\n"
                f"{keyword_label}: <code>{keyword_summary}</code>\n"
                f"{city_label}: <code>{city_summary}</code>\n"
                f"Найдено VK: {search.found_total}\n"
                f"Подошло по названию: {search.matched_total}\n"
                f"С беседами: {search.with_chat_total}\n"
                f"Без бесед: {search.without_chat_total}{limit_note}"
            )
            rows = []
            if search.with_chat_total:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"Беседы ({search.with_chat_total})",
                            callback_data=f"results:{search.id}:1:0",
                        )
                    ]
                )
            if search.without_chat_total:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"Без бесед ({search.without_chat_total})",
                            callback_data=f"results:{search.id}:0:0",
                        )
                    ]
                )
            keyboard = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
        elif search.status == SearchStatus.CANCELLED.value:
            text = "⛔ Поиск отменён."
            keyboard = None
        else:
            error = html.escape(search.error_message or "Неизвестная ошибка")
            text = f"❌ Поиск завершился с ошибкой.\n{error}"
            keyboard = None
        # A failed final edit must not terminate the sequential search worker.
        with suppress(TelegramAPIError):
            await self._bot.edit_message_text(
                text,
                chat_id=search.telegram_chat_id,
                message_id=search.telegram_message_id,
                reply_markup=keyboard,
            )

    async def _process(self, search: SearchRun) -> None:
        try:
            result = await self._processor.run(search, self._edit_progress)
        except (VKAuthenticationError, VKCaptchaError) as exc:
            await self._repository.update_search(
                search.id,
                status=SearchStatus.FAILED.value,
                progress_stage="failed",
                error_message=str(exc),
                completed_at=datetime.now(UTC),
            )
            result = await self._repository.get_search(search.id)
        except VKError as exc:
            await self._repository.update_search(
                search.id,
                status=SearchStatus.FAILED.value,
                progress_stage="failed",
                error_message=str(exc),
                completed_at=datetime.now(UTC),
            )
            result = await self._repository.get_search(search.id)
        except Exception:
            logger.exception("Unhandled search failure search_id=%s", search.id)
            await self._repository.update_search(
                search.id,
                status=SearchStatus.FAILED.value,
                progress_stage="failed",
                error_message="Внутренняя ошибка. Подробности записаны в журнал.",
                completed_at=datetime.now(UTC),
            )
            result = await self._repository.get_search(search.id)
        if result:
            await self._notify_finished(result)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            search: SearchRun | None = None
            try:
                search = await self._repository.claim_next_search()
                if search is None:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=2.0)
                    except TimeoutError:
                        pass
                    continue
                await self._process(search)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Search worker loop recovered after an unexpected failure search_id=%s",
                    search.id if search else None,
                )
                if search is not None:
                    with suppress(Exception):
                        current = await self._repository.get_search(search.id)
                        if current and current.status == SearchStatus.RUNNING.value:
                            await self._repository.update_search(
                                search.id,
                                status=SearchStatus.FAILED.value,
                                progress_stage="failed",
                                error_message=(
                                    "Внутренняя ошибка. "
                                    "Фоновый обработчик продолжил работу."
                                ),
                                completed_at=datetime.now(UTC),
                            )
                await asyncio.sleep(1.0)
