from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove, TelegramObject

from vk_chat_bot.repositories import Repository


class AccessMiddleware(BaseMiddleware):
    def __init__(self, repository: Repository, admin_ids: frozenset[int]) -> None:
        self._repository = repository
        self._admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)) or event.from_user is None:
            return await handler(event, data)

        sender = event.from_user
        user = await self._repository.upsert_user(sender.id, sender.username, sender.first_name)
        if sender.id in self._admin_ids or user.has_access:
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer("Нет доступа к боту.", show_alert=True)
        else:
            await event.answer(
                "Нет доступа к боту.\n"
                f"Ваш Telegram ID: <code>{sender.id}</code>\n"
                "Передайте этот ID администратору.",
                reply_markup=ReplyKeyboardRemove(),
            )
        return None
