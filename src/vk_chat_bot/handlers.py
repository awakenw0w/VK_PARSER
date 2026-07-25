from __future__ import annotations

import html
import math

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from vk_chat_bot.keyboards import MAIN_MENU
from vk_chat_bot.models import SearchRun, SearchStatus, User
from vk_chat_bot.repositories import Repository, ResultCard
from vk_chat_bot.text import normalize_text, validate_search_term
from vk_chat_bot.worker import SearchWorker

PAGE_SIZE = 10
KEYWORD_PAGE_SIZE = 8
CITY_PAGE_SIZE = 20


class InputStates(StatesGroup):
    personal_keyword = State()
    admin_keyword = State()
    admin_access_grant = State()
    admin_access_revoke = State()
    city_search = State()
    city_manual = State()
    random_city_count = State()
    library_title = State()
    library_keyword = State()
    library_city = State()


def _pagination(prefix: str, page: int, total: int, page_size: int) -> list[InlineKeyboardButton]:
    pages = max(1, math.ceil(total / page_size))
    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="←", callback_data=f"{prefix}:{page - 1}"))
    buttons.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
    if page + 1 < pages:
        buttons.append(InlineKeyboardButton(text="→", callback_data=f"{prefix}:{page + 1}"))
    return buttons


def _result_text(cards: list[ResultCard], *, page: int, total: int, title: str) -> str:
    parts = [f"<b>{html.escape(title)}</b>", f"Найдено: {total} · страница {page + 1}"]
    for index, card in enumerate(cards, start=page * PAGE_SIZE + 1):
        closed = " · закрытое" if card.is_closed else ""
        block = [
            f'\n<b>{index}. <a href="{html.escape(card.url, quote=True)}">{html.escape(card.name)}</a></b>{closed}',
            f"Ключ: <code>{html.escape(card.keyword)}</code> · город: <code>{html.escape(card.city)}</code>",
            f"Найдено: {card.discovered_at:%d.%m.%Y %H:%M}",
        ]
        for link in card.links:
            source = ", ".join(link.sources) if link.sources else "источник не указан"
            block.append(
                f'• <a href="{html.escape(link.url, quote=True)}">вступить в беседу</a> — {html.escape(source)}'
            )
        candidate = "\n".join(parts + block)
        if len(candidate) > 3900:
            parts.append("\n…страница сокращена из-за лимита Telegram.")
            break
        parts.extend(block)
    return "\n".join(parts)


def _results_keyboard(
    *, prefix: str, page: int, total: int, page_size: int = PAGE_SIZE
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if total > page_size:
        rows.append(_pagination(prefix, page, total, page_size))
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _history_label(search: SearchRun) -> str:
    labels = {
        SearchStatus.QUEUED.value: "⏳",
        SearchStatus.RUNNING.value: "🔎",
        SearchStatus.COMPLETED.value: "✅",
        SearchStatus.FAILED.value: "❌",
        SearchStatus.CANCELLED.value: "⛔",
    }
    return f"{labels.get(search.status, '•')} {search.keyword_label} · {search.city_label}"


def build_router(repository: Repository, worker: SearchWorker, admin_ids: frozenset[int]) -> Router:
    router = Router(name="bot")
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")

    async def ensure_user(message: Message) -> User:
        sender = message.from_user
        if sender is None:
            raise RuntimeError("Telegram user is unavailable")
        return await repository.upsert_user(sender.id, sender.username, sender.first_name)

    async def show_keyword_picker(
        message: Message, user: User, state: FSMContext, *, page: int = 0
    ) -> None:
        keywords = await repository.list_keywords(user.id)
        data = await state.get_data()
        selected = [str(value) for value in data.get("selected_keywords") or []]
        selected_normalized = {normalize_text(value) for value in selected}
        await state.update_data(keyword_page=page, keyword_selection_active=True)
        start = page * KEYWORD_PAGE_SIZE
        rows = [
            [
                InlineKeyboardButton(
                    text=("✅ " if item.normalized_text in selected_normalized else "")
                    + item.text,
                    callback_data=f"keyword:{item.id}",
                )
            ]
            for item in keywords[start : start + KEYWORD_PAGE_SIZE]
        ]
        if len(keywords) > KEYWORD_PAGE_SIZE:
            rows.append(_pagination("kwpage", page, len(keywords), KEYWORD_PAGE_SIZE))
        if keywords:
            all_selected = all(
                item.normalized_text in selected_normalized for item in keywords
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🗑 Снять все" if all_selected else "☑️ Выбрать все",
                        callback_data="keywords:clear" if all_selected else "keywords:all",
                    )
                ]
            )
        rows.append(
            [InlineKeyboardButton(text="➕ Добавить своё слово", callback_data="userkw:add")]
        )
        if selected:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ Продолжить ({len(selected)})",
                        callback_data="keywords:continue",
                    )
                ]
            )
        await message.answer(
            f"<b>Выберите ключевые слова</b>\n"
            f"Всего доступно: <b>{len(keywords)}</b>\n"
            f"Выбрано: <b>{len(selected)}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def show_city_menu(message: Message, state: FSMContext, user: User) -> None:
        recent = await repository.recent_cities(user.id)
        await state.update_data(recent_cities=recent)
        data = await state.get_data()
        selected = [str(city) for city in data.get("selected_cities") or []]
        total_cities = await repository.city_count()
        rows = [
            [
                InlineKeyboardButton(text="🔤 Найти в справочнике", callback_data="city:search"),
                InlineKeyboardButton(text="✍️ Ввести вручную", callback_data="city:manual"),
            ],
            [
                InlineKeyboardButton(text="📚 Список по буквам", callback_data="city:letters"),
                InlineKeyboardButton(text="🎲 Случайные города", callback_data="city:random"),
            ],
            [
                InlineKeyboardButton(
                    text="← Изменить ключевые слова",
                    callback_data="search:keywords_back",
                )
            ],
        ]
        for index, city in enumerate(recent):
            rows.append([InlineKeyboardButton(text=f"🕘 {city}", callback_data=f"recent:{index}")])
        if selected:
            start = max(0, len(selected) - 5)
            for index in range(start, len(selected)):
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"❌ {selected[index][:45]}",
                            callback_data=f"cityremove:{index}",
                        )
                    ]
                )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ Продолжить ({len(selected)})",
                        callback_data="search:review",
                    ),
                    InlineKeyboardButton(text="🗑 Очистить", callback_data="cities:clear"),
                ]
            )
        preview = ""
        if selected:
            visible = ", ".join(html.escape(city) for city in selected[:8])
            suffix = f" и ещё {len(selected) - 8}" if len(selected) > 8 else ""
            preview = f"\nВыбрано: <b>{len(selected)}</b>\n{visible}{suffix}"
        await message.answer(
            f"<b>Выберите города</b>\n"
            f"В справочнике: <b>{total_cities}</b>{preview}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def add_selected_cities(
        state: FSMContext, values: list[tuple[str, int | None]]
    ) -> int:
        data = await state.get_data()
        selected = [str(city) for city in data.get("selected_cities") or []]
        city_ids = {
            str(key): int(value) for key, value in (data.get("selected_city_ids") or {}).items()
        }
        normalized_seen = {normalize_text(city) for city in selected}
        added = 0
        for city, city_id in values:
            normalized = normalize_text(city)
            if not normalized or normalized in normalized_seen:
                if city_id is not None:
                    city_ids[normalized] = city_id
                continue
            selected.append(city)
            normalized_seen.add(normalized)
            if city_id is not None:
                city_ids[normalized] = city_id
            added += 1
        await state.update_data(selected_cities=selected, selected_city_ids=city_ids)
        return added

    async def show_confirmation(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        keywords = [str(value) for value in data.get("selected_keywords") or []]
        cities = [str(city) for city in data.get("selected_cities") or []]
        if not keywords or not cities:
            await message.answer("Выберите ключевые слова и города.")
            return
        visible_keywords = ", ".join(html.escape(keyword) for keyword in keywords[:12])
        if len(keywords) > 12:
            visible_keywords += f" и ещё {len(keywords) - 12}"
        visible_cities = "\n".join(f"• {html.escape(city)}" for city in cities[:15])
        if len(cities) > 15:
            visible_cities += f"\n…и ещё {len(cities) - 15}"
        query_count = sum(
            len(dict.fromkeys((f"{keyword} {city}", f"{city} {keyword}")))
            for keyword in keywords
            for city in cities
        )
        warning = ""
        if query_count >= 200:
            warning = "\n\n⚠️ Такой поиск может занять много времени."
        await message.answer(
            "<b>Запустить поиск?</b>\n"
            f"Ключей: <b>{len(keywords)}</b>\n{visible_keywords}\n"
            f"Городов: <b>{len(cities)}</b>\n{visible_cities}\n"
            f"VK-поисков: <b>{query_count}</b>"
            f"{warning}\n\nВ названии должны встречаться один из ключей и один из городов.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🚀 Запустить", callback_data="search:confirm")],
                    [InlineKeyboardButton(text="← Изменить города", callback_data="search:city_back")],
                ]
            ),
        )

    async def show_random_city_menu(message: Message, user: User) -> None:
        total, remaining = await repository.random_city_stats(user.id)
        quick_counts = [1, 5, 10, 25, 50, 100]
        buttons = [
            InlineKeyboardButton(text=str(count), callback_data=f"cityrandom:{count}")
            for count in quick_counts
            if count <= total
        ]
        rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=f"🎲 Все города ({total})",
                        callback_data="cityrandom:all",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="✍️ Ввести количество",
                        callback_data="cityrandom:custom",
                    )
                ],
            ]
        )
        await message.answer(
            "<b>Сколько случайных городов добавить?</b>\n"
            f"Всего в справочнике: <b>{total}</b>\n"
            f"Ещё не выпадало в текущем цикле: <b>{remaining}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def add_random_cities(
        message: Message, state: FSMContext, user: User, count: int
    ) -> None:
        data = await state.get_data()
        excluded_ids = {
            int(value) for value in (data.get("selected_city_ids") or {}).values()
        }
        cities = await repository.random_cities_for_user(
            user.id, count, exclude_city_ids=excluded_ids
        )
        added = await add_selected_cities(state, [(city.name, city.id) for city in cities])
        if added:
            await message.answer(f"🎲 Добавлено случайных городов: <b>{added}</b>.")
        else:
            await message.answer("Все доступные города уже выбраны.")
        await state.set_state(None)
        await show_city_menu(message, state, user)

    async def render_search_results(
        target: Message,
        *,
        telegram_user_id: int,
        search_id: int,
        with_chat: bool,
        page: int,
        edit: bool,
    ) -> None:
        user = await repository.get_user(telegram_user_id)
        if user is None:
            return
        cards, total = await repository.results_page(
            user_id=user.id,
            search_id=search_id,
            with_chat=with_chat,
            offset=page * PAGE_SIZE,
            limit=PAGE_SIZE,
        )
        title = "Группы со ссылками на беседы" if with_chat else "Группы без найденных бесед"
        text = _result_text(cards, page=page, total=total, title=title)
        keyboard = _results_keyboard(
            prefix=f"results:{search_id}:{int(with_chat)}", page=page, total=total
        )
        if edit:
            await target.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
        else:
            await target.answer(text, reply_markup=keyboard, disable_web_page_preview=True)

    async def render_library(
        target: Message,
        *,
        telegram_user_id: int,
        section: str,
        page: int,
        state: FSMContext,
        edit: bool,
    ) -> None:
        user = await repository.get_user(telegram_user_id)
        if user is None:
            return
        data = await state.get_data()
        filters = data.get("library_filters") or {}
        with_chat = None if section == "a" else section == "1"
        cards, total = await repository.library_page(
            user_id=user.id,
            with_chat=with_chat,
            title_query=str(filters.get("title") or ""),
            keyword=str(filters.get("keyword") or ""),
            city=str(filters.get("city") or ""),
            offset=page * PAGE_SIZE,
            limit=PAGE_SIZE,
        )
        title = {"1": "Моя база · с беседами", "0": "Моя база · без бесед", "a": "Моя база"}[
            section
        ]
        text = _result_text(cards, page=page, total=total, title=title)
        keyboard = _results_keyboard(prefix=f"library:{section}", page=page, total=total)
        if edit:
            await target.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
        else:
            await target.answer(text, reply_markup=keyboard, disable_web_page_preview=True)

    async def show_database_menu(message: Message, state: FSMContext) -> None:
        await state.clear()
        rows = [
            [
                InlineKeyboardButton(text="💬 С беседами", callback_data="database:1"),
                InlineKeyboardButton(text="📁 Без бесед", callback_data="database:0"),
            ],
            [InlineKeyboardButton(text="Все сообщества", callback_data="database:a")],
            [
                InlineKeyboardButton(text="По названию", callback_data="dbfilter:title"),
                InlineKeyboardButton(text="По ключу", callback_data="dbfilter:keyword"),
                InlineKeyboardButton(text="По городу", callback_data="dbfilter:city"),
            ],
        ]
        await message.answer(
            "<b>Моя база</b>\nВыберите раздел или один фильтр.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await ensure_user(message)
        await message.answer(
            "Привет! Я ищу VK-сообщества по ключевым словам и городам, затем нахожу ссылки на беседы в данных группы и закрепе.",
            reply_markup=MAIN_MENU,
        )

    @router.message(Command("help"))
    @router.message(F.text == "ℹ️ Помощь")
    async def help_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await ensure_user(message)
        await message.answer(
            "<b>Как пользоваться</b>\n"
            "1. Нажмите «Новый поиск».\n"
            "2. Выберите один или несколько ключей и городов.\n"
            "3. Дождитесь результата.\n\n"
            "Проверяются описание, статус, сайт, блок ссылок и только закреплённая запись. Обычная стена не сканируется. Поддерживаются приглашения vk.com/join, vk.ru/join и vk.me/join.",
            reply_markup=MAIN_MENU,
        )

    @router.message(Command("search"))
    @router.message(F.text == "🔎 Новый поиск")
    async def new_search(message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await ensure_user(message)
        active = await repository.active_search_for_user(user.id)
        if active:
            await message.answer("У вас уже выполняется поиск. Используйте /cancel для отмены.")
            return
        await show_keyword_picker(message, user, state)

    @router.callback_query(F.data.startswith("kwpage:"))
    async def keyword_page(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.message or not callback.from_user:
            return
        user = await repository.get_user(callback.from_user.id)
        if user is None:
            return
        page = int(callback.data.rsplit(":", 1)[1])
        await show_keyword_picker(callback.message, user, state, page=page)

    @router.callback_query(F.data.startswith("keyword:"))
    async def keyword_selected(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not callback.from_user:
            await callback.answer()
            return
        user = await repository.get_user(callback.from_user.id)
        if user is None:
            await callback.answer()
            return
        keyword_id = int(callback.data.split(":", 1)[1])
        keyword = await repository.get_keyword_for_user(keyword_id, user.id)
        if keyword is None:
            await callback.answer("Ключевое слово недоступно", show_alert=True)
            return
        await callback.answer()
        data = await state.get_data()
        selected = [str(value) for value in data.get("selected_keywords") or []]
        normalized = normalize_text(keyword.text)
        existing_index = next(
            (index for index, value in enumerate(selected) if normalize_text(value) == normalized),
            None,
        )
        if existing_index is None:
            selected.append(keyword.text)
        else:
            selected.pop(existing_index)
        await state.update_data(selected_keywords=selected)
        await show_keyword_picker(
            callback.message, user, state, page=int(data.get("keyword_page") or 0)
        )

    @router.callback_query(F.data == "keywords:all")
    async def keywords_select_all(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer("Выбраны все ключевые слова")
        if not callback.message or not callback.from_user:
            return
        user = await repository.get_user(callback.from_user.id)
        if user is None:
            return
        keywords = await repository.list_keywords(user.id)
        await state.update_data(selected_keywords=[item.text for item in keywords])
        data = await state.get_data()
        await show_keyword_picker(
            callback.message, user, state, page=int(data.get("keyword_page") or 0)
        )

    @router.callback_query(F.data == "keywords:clear")
    async def keywords_clear(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer("Выбор снят")
        await state.update_data(selected_keywords=[])
        if callback.message and callback.from_user:
            user = await repository.get_user(callback.from_user.id)
            if user:
                data = await state.get_data()
                await show_keyword_picker(
                    callback.message, user, state, page=int(data.get("keyword_page") or 0)
                )

    @router.callback_query(F.data == "keywords:continue")
    async def keywords_continue(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not callback.from_user:
            await callback.answer()
            return
        data = await state.get_data()
        if not data.get("selected_keywords"):
            await callback.answer("Выберите хотя бы одно слово", show_alert=True)
            return
        user = await repository.get_user(callback.from_user.id)
        await callback.answer()
        if user:
            await show_city_menu(callback.message, state, user)

    @router.callback_query(F.data == "userkw:add")
    async def personal_keyword_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(InputStates.personal_keyword)
        if callback.message:
            await callback.message.answer("Отправьте новое личное ключевое слово.")

    @router.message(InputStates.personal_keyword, ~F.text.startswith("/"))
    async def personal_keyword_save(message: Message, state: FSMContext) -> None:
        user = await ensure_user(message)
        try:
            value = validate_search_term(message.text or "")
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await repository.add_keyword(value, user_id=user.id)
        data = await state.get_data()
        selected = [str(item) for item in data.get("selected_keywords") or []]
        if data.get("keyword_selection_active") and normalize_text(value) not in {
            normalize_text(item) for item in selected
        }:
            selected.append(value)
        await state.update_data(selected_keywords=selected)
        await state.set_state(None)
        await message.answer(f"Ключ <code>{html.escape(value)}</code> сохранён.")
        await show_keyword_picker(message, user, state)

    @router.message(Command("keywords"))
    @router.message(F.text == "🧩 Мои ключевые слова")
    async def keyword_manager(message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await ensure_user(message)
        keywords = await repository.list_keywords(user.id)
        personal = [item for item in keywords if item.owner_user_id == user.id]
        global_items = [item.text for item in keywords if item.owner_user_id is None]
        rows = [
            [InlineKeyboardButton(text=f"🗑 {item.text}", callback_data=f"userkwdel:{item.id}")]
            for item in personal
        ]
        rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="userkw:add")])
        await message.answer(
            "<b>Общие слова</b>: "
            + (", ".join(html.escape(item) for item in global_items) or "нет")
            + "\n\n<b>Личные слова</b>: нажмите на слово, чтобы удалить.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @router.callback_query(F.data.startswith("userkwdel:"))
    async def personal_keyword_delete(callback: CallbackQuery) -> None:
        if not callback.from_user:
            return
        user = await repository.get_user(callback.from_user.id)
        deleted = False
        if user:
            deleted = await repository.delete_keyword(
                int(callback.data.split(":", 1)[1]), user_id=user.id
            )
        await callback.answer("Удалено" if deleted else "Слово не найдено", show_alert=not deleted)
        if deleted and callback.message:
            await callback.message.delete()

    @router.callback_query(F.data == "city:search")
    async def city_search_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(InputStates.city_search)
        if callback.message:
            await callback.message.answer("Введите часть названия города.")

    @router.message(InputStates.city_search, ~F.text.startswith("/"))
    async def city_search_result(message: Message, state: FSMContext) -> None:
        query = (message.text or "").strip()
        if len(query) < 2:
            await message.answer("Введите хотя бы 2 символа.")
            return
        cities = await repository.search_cities(query)
        await state.update_data(typed_city=query)
        rows = [
            [InlineKeyboardButton(text=city.name, callback_data=f"cityid:{city.id}")]
            for city in cities
        ]
        rows.append(
            [InlineKeyboardButton(text=f"Использовать «{query[:30]}»", callback_data="city:typed")]
        )
        await message.answer(
            "Выберите вариант или используйте введённый текст:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @router.callback_query(F.data == "city:manual")
    async def city_manual_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(InputStates.city_manual)
        if callback.message:
            await callback.message.answer("Введите город вручную.")

    @router.message(InputStates.city_manual, ~F.text.startswith("/"))
    async def city_manual_save(message: Message, state: FSMContext) -> None:
        try:
            city = validate_search_term(message.text or "", maximum=128)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        user = await ensure_user(message)
        await add_selected_cities(state, [(city, None)])
        await state.set_state(None)
        await show_city_menu(message, state, user)

    @router.callback_query(F.data == "city:typed")
    async def city_typed_selected(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.message:
            return
        data = await state.get_data()
        city = validate_search_term(str(data.get("typed_city") or ""), maximum=128)
        user = await repository.get_user(callback.from_user.id)
        if user is None:
            return
        await add_selected_cities(state, [(city, None)])
        await state.set_state(None)
        await show_city_menu(callback.message, state, user)

    @router.callback_query(F.data == "city:random")
    async def city_random(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if callback.message and callback.from_user:
            user = await repository.get_user(callback.from_user.id)
            if user:
                await show_random_city_menu(callback.message, user)

    @router.callback_query(F.data == "cityrandom:custom")
    async def random_city_custom_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(InputStates.random_city_count)
        if callback.message and callback.from_user:
            user = await repository.get_user(callback.from_user.id)
            if user:
                total, _ = await repository.random_city_stats(user.id)
                await callback.message.answer(
                    f"Введите число от 1 до {total}."
                )

    @router.message(InputStates.random_city_count, ~F.text.startswith("/"))
    async def random_city_custom_save(message: Message, state: FSMContext) -> None:
        user = await ensure_user(message)
        total, _ = await repository.random_city_stats(user.id)
        raw_count = (message.text or "").strip()
        if not raw_count.isdigit() or not 1 <= int(raw_count) <= total:
            await message.answer(f"Введите целое число от 1 до {total}.")
            return
        await add_random_cities(message, state, user, int(raw_count))

    @router.callback_query(F.data.startswith("cityrandom:"))
    async def random_city_count_selected(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not callback.from_user:
            await callback.answer()
            return
        user = await repository.get_user(callback.from_user.id)
        if user is None:
            await callback.answer()
            return
        total, _ = await repository.random_city_stats(user.id)
        raw_count = callback.data.split(":", 1)[1]
        count = total if raw_count == "all" else int(raw_count)
        await callback.answer()
        await add_random_cities(callback.message, state, user, count)

    @router.callback_query(F.data == "city:letters")
    async def city_letters(callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.message:
            return
        alphabet = "АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯ"
        buttons = [
            InlineKeyboardButton(text=letter, callback_data=f"letter:{letter}:0")
            for letter in alphabet
        ]
        rows = [buttons[index : index + 6] for index in range(0, len(buttons), 6)]
        await callback.message.answer(
            "Выберите первую букву:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @router.callback_query(F.data.startswith("letter:"))
    async def city_letter_page(callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.message:
            return
        _, letter, page_raw = callback.data.split(":")
        page = int(page_raw)
        cities, total = await repository.list_cities_by_letter(
            letter, offset=page * CITY_PAGE_SIZE, limit=CITY_PAGE_SIZE
        )
        rows = [
            [InlineKeyboardButton(text=city.name, callback_data=f"cityid:{city.id}")]
            for city in cities
        ]
        if total > CITY_PAGE_SIZE:
            rows.append(_pagination(f"letter:{letter}", page, total, CITY_PAGE_SIZE))
        await callback.message.edit_text(
            f"Города на «{letter}»:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @router.callback_query(F.data.startswith("cityid:"))
    async def city_id_selected(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.message:
            return
        city_id = int(callback.data.split(":", 1)[1])
        city = await repository.get_city(city_id)
        user = await repository.get_user(callback.from_user.id)
        if city and user:
            await add_selected_cities(state, [(city.name, city.id)])
            await state.set_state(None)
            await show_city_menu(callback.message, state, user)

    @router.callback_query(F.data.startswith("recent:"))
    async def recent_city_selected(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.message:
            return
        data = await state.get_data()
        recent = data.get("recent_cities") or []
        index = int(callback.data.split(":", 1)[1])
        if 0 <= index < len(recent):
            user = await repository.get_user(callback.from_user.id)
            if user:
                await add_selected_cities(state, [(str(recent[index]), None)])
                await show_city_menu(callback.message, state, user)

    @router.callback_query(F.data.startswith("cityremove:"))
    async def city_remove(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.message or not callback.from_user:
            return
        data = await state.get_data()
        selected = [str(city) for city in data.get("selected_cities") or []]
        city_ids = dict(data.get("selected_city_ids") or {})
        index = int(callback.data.split(":", 1)[1])
        if 0 <= index < len(selected):
            removed = selected.pop(index)
            city_ids.pop(normalize_text(removed), None)
            await state.update_data(selected_cities=selected, selected_city_ids=city_ids)
        user = await repository.get_user(callback.from_user.id)
        if user:
            await show_city_menu(callback.message, state, user)

    @router.callback_query(F.data == "cities:clear")
    async def cities_clear(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer("Список очищен")
        await state.update_data(selected_cities=[], selected_city_ids={})
        if callback.message and callback.from_user:
            user = await repository.get_user(callback.from_user.id)
            if user:
                await show_city_menu(callback.message, state, user)

    @router.callback_query(F.data == "search:review")
    async def search_review(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if callback.message:
            await show_confirmation(callback.message, state)

    @router.callback_query(F.data == "search:city_back")
    async def city_back(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.message or not callback.from_user:
            return
        user = await repository.get_user(callback.from_user.id)
        if user:
            await show_city_menu(callback.message, state, user)

    @router.callback_query(F.data == "search:keywords_back")
    async def keywords_back(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not callback.message or not callback.from_user:
            return
        user = await repository.get_user(callback.from_user.id)
        if user:
            data = await state.get_data()
            await show_keyword_picker(
                callback.message, user, state, page=int(data.get("keyword_page") or 0)
            )

    @router.callback_query(F.data == "search:confirm")
    async def search_confirm(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message or not callback.from_user:
            await callback.answer()
            return
        user = await repository.get_user(callback.from_user.id)
        if user is None:
            await callback.answer()
            return
        data = await state.get_data()
        keywords = [str(value) for value in data.get("selected_keywords") or []]
        cities = [str(city) for city in data.get("selected_cities") or []]
        if not keywords or not cities:
            await callback.answer("Сценарий устарел. Начните поиск заново.", show_alert=True)
            return
        try:
            search = await repository.create_search(
                user_id=user.id,
                keywords=keywords,
                cities=cities,
                telegram_chat_id=callback.message.chat.id,
            )
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.answer()
        progress = await callback.message.answer(
            worker.progress_text(search),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Отменить", callback_data=f"cancel:{search.id}")]
                ]
            ),
        )
        await repository.set_search_message(search.id, progress.message_id)
        await state.clear()
        worker.notify()

    @router.message(Command("cancel"))
    async def cancel_command(message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await ensure_user(message)
        search = await repository.request_cancel(user.id)
        await message.answer("Отмена запрошена." if search else "Активного поиска нет.")

    @router.callback_query(F.data.startswith("cancel:"))
    async def cancel_callback(callback: CallbackQuery) -> None:
        if not callback.from_user:
            return
        user = await repository.get_user(callback.from_user.id)
        search = await repository.request_cancel(user.id) if user else None
        await callback.answer(
            "Отмена запрошена" if search else "Поиск уже завершён", show_alert=True
        )

    @router.callback_query(F.data.startswith("results:"))
    async def results_callback(callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.message or not callback.from_user:
            return
        _, search_raw, chat_raw, page_raw = callback.data.split(":")
        await render_search_results(
            callback.message,
            telegram_user_id=callback.from_user.id,
            search_id=int(search_raw),
            with_chat=bool(int(chat_raw)),
            page=int(page_raw),
            edit=True,
        )

    @router.message(Command("history"))
    @router.message(F.text == "🕘 История")
    async def history(message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await ensure_user(message)
        items, total = await repository.list_history(user.id)
        rows = [
            [InlineKeyboardButton(text=_history_label(item), callback_data=f"history:{item.id}")]
            for item in items
        ]
        await message.answer(
            f"<b>История поиска</b>\nВсего запусков: {total}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
        )

    @router.callback_query(F.data.startswith("history:"))
    async def history_item(callback: CallbackQuery) -> None:
        if not callback.message or not callback.from_user:
            await callback.answer()
            return
        search_id = int(callback.data.split(":", 1)[1])
        search = await repository.get_search(search_id)
        user = await repository.get_user(callback.from_user.id)
        if search is None or user is None or search.user_id != user.id:
            await callback.answer("Запись недоступна", show_alert=True)
            return
        await callback.answer()
        text = (
            f"<b>{html.escape(_history_label(search))}</b>\n"
            f"Создан: {search.created_at:%d.%m.%Y %H:%M}\n"
            f"С беседами: {search.with_chat_total}\nБез бесед: {search.without_chat_total}"
        )
        rows = []
        if search.with_chat_total:
            rows.append(
                [InlineKeyboardButton(text="Беседы", callback_data=f"results:{search.id}:1:0")]
            )
        if search.without_chat_total:
            rows.append(
                [InlineKeyboardButton(text="Без бесед", callback_data=f"results:{search.id}:0:0")]
            )
        await callback.message.answer(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
        )

    @router.message(Command("database"))
    @router.message(F.text == "🗃 Моя база")
    async def database_menu(message: Message, state: FSMContext) -> None:
        await ensure_user(message)
        await show_database_menu(message, state)

    @router.callback_query(F.data.startswith("database:"))
    async def database_section(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if callback.message and callback.from_user:
            section = callback.data.split(":", 1)[1]
            await state.update_data(library_filters={})
            await render_library(
                callback.message,
                telegram_user_id=callback.from_user.id,
                section=section,
                page=0,
                state=state,
                edit=False,
            )

    @router.callback_query(F.data.startswith("library:"))
    async def library_page(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if callback.message and callback.from_user:
            _, section, page_raw = callback.data.split(":")
            await render_library(
                callback.message,
                telegram_user_id=callback.from_user.id,
                section=section,
                page=int(page_raw),
                state=state,
                edit=True,
            )

    @router.callback_query(F.data.startswith("dbfilter:"))
    async def database_filter_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        kind = callback.data.split(":", 1)[1]
        states = {
            "title": InputStates.library_title,
            "keyword": InputStates.library_keyword,
            "city": InputStates.library_city,
        }
        await state.set_state(states[kind])
        await state.update_data(filter_kind=kind)
        if callback.message:
            await callback.message.answer("Введите текст фильтра.")

    @router.message(
        StateFilter(
            InputStates.library_title,
            InputStates.library_keyword,
            InputStates.library_city,
        ),
        ~F.text.startswith("/"),
    )
    async def database_filter_apply(message: Message, state: FSMContext) -> None:
        await ensure_user(message)
        value = (message.text or "").strip()
        if len(value) < 2:
            await message.answer("Введите хотя бы 2 символа.")
            return
        data = await state.get_data()
        kind = str(data.get("filter_kind"))
        await state.update_data(library_filters={kind: value})
        await state.set_state(None)
        await render_library(
            message,
            telegram_user_id=message.from_user.id,
            section="a",
            page=0,
            state=state,
            edit=False,
        )

    async def show_admin_panel(message: Message) -> None:
        rows = [
            [
                InlineKeyboardButton(text="🔑 Выдать доступ", callback_data="adminaccess:grant"),
                InlineKeyboardButton(text="🚫 Забрать доступ", callback_data="adminaccess:revoke"),
            ],
            [InlineKeyboardButton(text="👥 Кому выдан доступ", callback_data="adminaccess:list")],
            [InlineKeyboardButton(text="🧩 Общий словарь", callback_data="admin:keywords")],
        ]
        await message.answer(
            "<b>Админ-панель</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def show_admin_keywords(message: Message) -> None:
        items = await repository.list_global_keywords()
        rows = [
            [InlineKeyboardButton(text=f"🗑 {item.text}", callback_data=f"adminkwdel:{item.id}")]
            for item in items
        ]
        rows.append(
            [InlineKeyboardButton(text="➕ Добавить общее слово", callback_data="adminkw:add")]
        )
        await message.answer(
            "<b>Общий словарь</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    @router.message(Command("admin"))
    async def admin_panel(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not message.from_user or message.from_user.id not in admin_ids:
            await message.answer("Команда доступна только администратору.")
            return
        await show_admin_panel(message)

    @router.message(Command("admin_keywords"))
    async def admin_keywords(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not message.from_user or message.from_user.id not in admin_ids:
            await message.answer("Команда доступна только администратору.")
            return
        await show_admin_keywords(message)

    @router.callback_query(F.data == "admin:keywords")
    async def admin_keywords_callback(callback: CallbackQuery) -> None:
        if callback.from_user.id not in admin_ids:
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer()
        if callback.message:
            await show_admin_keywords(callback.message)

    @router.callback_query(F.data.in_({"adminaccess:grant", "adminaccess:revoke"}))
    async def admin_access_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.from_user.id not in admin_ids:
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        granting = callback.data == "adminaccess:grant"
        await callback.answer()
        await state.set_state(
            InputStates.admin_access_grant if granting else InputStates.admin_access_revoke
        )
        if callback.message:
            action = "выдать" if granting else "забрать"
            await callback.message.answer(
                f"Отправьте Telegram ID пользователя, которому нужно {action} доступ."
            )

    @router.message(
        StateFilter(InputStates.admin_access_grant, InputStates.admin_access_revoke),
        ~F.text.startswith("/"),
    )
    async def admin_access_save(message: Message, state: FSMContext) -> None:
        if not message.from_user or message.from_user.id not in admin_ids:
            await state.clear()
            return
        raw_id = (message.text or "").strip()
        if not raw_id.isdigit() or int(raw_id) <= 0:
            await message.answer("Нужен числовой Telegram ID.")
            return
        target_id = int(raw_id)
        current_state = await state.get_state()
        granting = current_state == InputStates.admin_access_grant.state
        if not granting and target_id in admin_ids:
            await state.clear()
            await message.answer("Доступ администратора задан в конфигурации и не может быть отозван.")
            return
        await repository.set_user_access(target_id, allowed=granting)
        await state.clear()
        result = "выдан" if granting else "отозван"
        await message.answer(f"Доступ для <code>{target_id}</code> {result}.")
        await show_admin_panel(message)

    @router.callback_query(F.data == "adminaccess:list")
    async def admin_access_list(callback: CallbackQuery) -> None:
        if callback.from_user.id not in admin_ids:
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer()
        users = await repository.list_users_with_access()
        lines = ["<b>Доступ выдан</b>"]
        lines.extend(
            f"• <code>{user.telegram_id}</code>"
            + (f" — @{html.escape(user.username)}" if user.username else "")
            for user in users
        )
        if len(lines) == 1:
            lines.append("Нет добавленных пользователей.")
        if callback.message:
            await callback.message.answer("\n".join(lines))

    @router.callback_query(F.data == "adminkw:add")
    async def admin_keyword_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.from_user.id not in admin_ids:
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer()
        await state.set_state(InputStates.admin_keyword)
        if callback.message:
            await callback.message.answer("Отправьте новое общее ключевое слово.")

    @router.message(InputStates.admin_keyword, ~F.text.startswith("/"))
    async def admin_keyword_save(message: Message, state: FSMContext) -> None:
        if not message.from_user or message.from_user.id not in admin_ids:
            await state.clear()
            return
        try:
            value = validate_search_term(message.text or "")
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await repository.add_keyword(value)
        await state.clear()
        await message.answer(f"Общее слово <code>{html.escape(value)}</code> добавлено.")

    @router.callback_query(F.data.startswith("adminkwdel:"))
    async def admin_keyword_delete(callback: CallbackQuery) -> None:
        if callback.from_user.id not in admin_ids:
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        deleted = await repository.delete_keyword(
            int(callback.data.split(":", 1)[1]), global_only=True
        )
        await callback.answer("Удалено" if deleted else "Не найдено", show_alert=not deleted)
        if deleted and callback.message:
            await callback.message.delete()

    @router.callback_query(F.data == "main")
    async def main_callback(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.clear()
        if callback.message:
            await callback.message.answer("Главное меню", reply_markup=MAIN_MENU)

    @router.callback_query(F.data == "noop")
    async def noop(callback: CallbackQuery) -> None:
        await callback.answer()

    return router
