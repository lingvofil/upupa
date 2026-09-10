"""Shared inline UI for the World of Upupa main hub."""

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_world_main_markup() -> types.InlineKeyboardMarkup:
    """Return the single canonical main menu used by every World hub handler."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🏳 Моё государство", callback_data="worldhub:mine")
    builder.button(text="🌐 Государства", callback_data="worldhub:states")
    builder.button(text="🤝 Дипломатия", callback_data="worldhub:diplomacy")
    builder.button(text="🎩 Назначить посла", callback_data="worldx:ambassador")
    builder.button(text="🏴 Флаг / герб", callback_data="worldsymbol:menu")
    builder.button(text="🚫 Санкции", callback_data="worldhub:sanctions")
    builder.button(text="⚖️ Международный суд", callback_data="worldhub:court")
    builder.button(text="🗺 Карта мира", callback_data="worldhub:map")
    builder.button(text="📰 Мировые новости", callback_data="worldhub:news")
    builder.button(text="📜 Хроника", callback_data="worldhub:chronicle")
    builder.adjust(2, 2, 2, 2, 2)
    return builder.as_markup()
