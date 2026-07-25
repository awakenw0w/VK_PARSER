from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vk_chat_bot.db import create_engine, create_schema, create_session_factory
from vk_chat_bot.repositories import Repository


@pytest_asyncio.fixture
async def database() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession], Repository]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    await create_schema(engine)
    sessions = create_session_factory(engine)
    repository = Repository(sessions)
    try:
        yield engine, sessions, repository
    finally:
        await engine.dispose()
