from __future__ import annotations

import asyncio
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from alembic import command
from alembic.config import Config

from vk_chat_bot.access import AccessMiddleware
from vk_chat_bot.cities import seed_cities
from vk_chat_bot.config import Settings, get_settings
from vk_chat_bot.db import create_engine, create_session_factory, ensure_sqlite_directory
from vk_chat_bot.handlers import build_router
from vk_chat_bot.logging_config import configure_logging
from vk_chat_bot.repositories import Repository
from vk_chat_bot.search_service import SearchProcessor
from vk_chat_bot.vk_client import VKClient
from vk_chat_bot.worker import SearchWorker


def upgrade_database(database_url: str) -> None:
    ensure_sqlite_directory(database_url)
    root = Path.cwd()
    if not (root / "alembic.ini").exists():
        root = next(
            (
                parent
                for parent in Path(__file__).resolve().parents
                if (parent / "alembic.ini").exists()
            ),
            root,
        )
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="search", description="Новый поиск"),
            BotCommand(command="keywords", description="Мои ключевые слова"),
            BotCommand(command="database", description="Моя база"),
            BotCommand(command="history", description="История"),
            BotCommand(command="cancel", description="Отменить поиск"),
            BotCommand(command="help", description="Помощь"),
        ]
    )


async def main(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    await asyncio.to_thread(upgrade_database, settings.database_url)

    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    repository = Repository(sessions)
    await seed_cities(repository)

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    vk = VKClient(
        access_token=settings.vk_user_access_token,
        api_version=settings.vk_api_version,
        requests_per_second=settings.vk_requests_per_second,
    )
    processor = SearchProcessor(repository=repository, session_factory=sessions, vk=vk)
    worker = SearchWorker(repository=repository, processor=processor, bot=bot)
    dispatcher = Dispatcher()
    access_middleware = AccessMiddleware(repository, settings.admin_telegram_ids)
    dispatcher.message.outer_middleware(access_middleware)
    dispatcher.callback_query.outer_middleware(access_middleware)
    dispatcher.include_router(build_router(repository, worker, settings.admin_telegram_ids))

    await bot.delete_webhook(drop_pending_updates=False)
    await set_commands(bot)
    await worker.start()
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await worker.stop()
        await vk.close()
        await bot.session.close()
        await engine.dispose()


def run() -> None:
    asyncio.run(main())
