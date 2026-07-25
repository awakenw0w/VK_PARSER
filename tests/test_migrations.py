from __future__ import annotations

import sqlite3

from vk_chat_bot.app import upgrade_database


def test_alembic_upgrade_creates_expected_tables(tmp_path) -> None:
    database = tmp_path / "migration.db"
    upgrade_database(f"sqlite+aiosqlite:///{database.as_posix()}")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type = 'table'")
        }
    assert {
        "alembic_version",
        "users",
        "cities",
        "keywords",
        "communities",
        "search_runs",
        "search_results",
        "chat_links",
    } <= tables
    with sqlite3.connect(database) as connection:
        keywords = connection.execute(
            "select text from keywords where scope = 'global' order by normalized_text"
        ).fetchall()
    assert {row[0] for row in keywords} == {
        "работа",
        "подработка",
        "шабашка",
        "доска объявлений",
        "услуги",
        "взаимопомощь",
        "барахолка",
        "куплю",
        "продам",
        "куплю-продам",
        "ярмарка",
        "подслушано",
        "новости",
    }
