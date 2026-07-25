from __future__ import annotations

from vk_chat_bot.config import Settings


def test_single_and_multiple_admin_ids_are_parsed() -> None:
    single = Settings(
        telegram_bot_token="1" * 21,
        vk_user_access_token="v" * 8,
        admin_telegram_ids=123456789,
    )
    multiple = Settings(
        telegram_bot_token="1" * 21,
        vk_user_access_token="v" * 8,
        admin_telegram_ids="1, 2",
    )
    assert single.admin_telegram_ids == {123456789}
    assert multiple.admin_telegram_ids == {1, 2}
