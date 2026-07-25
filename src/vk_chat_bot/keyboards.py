from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔎 Новый поиск")],
        [KeyboardButton(text="🧩 Мои ключевые слова"), KeyboardButton(text="🗃 Моя база")],
        [KeyboardButton(text="🕘 История"), KeyboardButton(text="ℹ️ Помощь")],
    ],
    resize_keyboard=True,
    is_persistent=True,
    one_time_keyboard=False,
    input_field_placeholder="Выберите действие",
)
