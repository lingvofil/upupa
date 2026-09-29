"""Настройки выбора голоса для команды «переозвучь»."""

from __future__ import annotations

import math

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.state import chat_settings
from features.chat_settings import save_chat_settings
from services.elevenlabs import ElevenLabsError
from services.revoice import REVOICE_RANDOM_VOICE_ID, get_revoice_voices


REVOICE_VOICES_PAGE_SIZE = 10


def get_revoice_selection_label(chat_id: str) -> str:
    """Короткая подпись текущего режима без обращения к ElevenLabs."""
    settings = chat_settings.get(str(chat_id), {})
    voice_id = str(
        settings.get("revoice_voice_id", REVOICE_RANDOM_VOICE_ID)
        or REVOICE_RANDOM_VOICE_ID
    )
    if voice_id == REVOICE_RANDOM_VOICE_ID:
        return "🎲 Рандом"
    return str(settings.get("revoice_voice_name") or "Выбранный голос")


async def get_revoice_settings_markup(
    chat_id: str,
    *,
    page: int = 0,
    force_refresh: bool = False,
):
    """Показать Random + все голоса, реально доступные текущему API-ключу."""
    settings = chat_settings.get(str(chat_id), {})
    current_id = str(
        settings.get("revoice_voice_id", REVOICE_RANDOM_VOICE_ID)
        or REVOICE_RANDOM_VOICE_ID
    )
    current_label = get_revoice_selection_label(chat_id)

    builder = InlineKeyboardBuilder()

    try:
        voices = await get_revoice_voices(force_refresh=force_refresh)
    except ElevenLabsError:
        text = (
            "🎙 *Переозвучка*\n\n"
            f"Сейчас: {current_label}\n\n"
            "Не удалось загрузить список голосов ElevenLabs."
        )
        builder.row(
            types.InlineKeyboardButton(
                text="🔄 Повторить",
                callback_data="settings:revoice:refresh:0",
            )
        )
        builder.row(
            types.InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="settings:view:main",
            )
        )
        return text, builder.as_markup()

    voices = sorted(voices, key=lambda voice: voice.name.casefold())
    total_pages = max(1, math.ceil(len(voices) / REVOICE_VOICES_PAGE_SIZE))
    page = max(0, min(int(page), total_pages - 1))

    text = (
        "🎙 *Переозвучка*\n\n"
        f"Сейчас: {current_label}\n"
        f"Доступно голосов: *{len(voices)}*\n\n"
        "Выбери голос для команды «переозвучь»."
    )
    if total_pages > 1:
        text += f"\nСтраница *{page + 1}/{total_pages}*."

    random_marker = "✅ " if current_id == REVOICE_RANDOM_VOICE_ID else ""
    builder.row(
        types.InlineKeyboardButton(
            text=f"{random_marker}🎲 Рандом",
            callback_data=f"settings:revoice:set:{REVOICE_RANDOM_VOICE_ID}",
        )
    )

    start = page * REVOICE_VOICES_PAGE_SIZE
    page_voices = voices[start : start + REVOICE_VOICES_PAGE_SIZE]
    buttons = []
    for voice in page_voices:
        marker = "✅ " if voice.voice_id == current_id else ""
        name = voice.name.strip() or voice.voice_id
        if len(name) > 30:
            name = name[:29] + "…"
        buttons.append(
            types.InlineKeyboardButton(
                text=f"{marker}{name}",
                callback_data=f"settings:revoice:set:{voice.voice_id}",
            )
        )

    for index in range(0, len(buttons), 2):
        builder.row(*buttons[index : index + 2])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            types.InlineKeyboardButton(
                text="⬅️",
                callback_data=f"settings:revoice:page:{page - 1}",
            )
        )
    if page + 1 < total_pages:
        nav_buttons.append(
            types.InlineKeyboardButton(
                text="➡️",
                callback_data=f"settings:revoice:page:{page + 1}",
            )
        )
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(
        types.InlineKeyboardButton(
            text="🔄 Обновить список",
            callback_data=f"settings:revoice:refresh:{page}",
        )
    )
    builder.row(
        types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="settings:view:main",
        )
    )
    return text, builder.as_markup()


async def set_revoice_voice(chat_id: str, voice_id: str) -> str:
    """Проверить выбранный voice_id, сохранить его и вернуть display name."""
    chat_id = str(chat_id)
    normalized_id = str(voice_id or "").strip()

    chat_settings.setdefault(chat_id, {})

    if normalized_id == REVOICE_RANDOM_VOICE_ID:
        chat_settings[chat_id]["revoice_voice_id"] = REVOICE_RANDOM_VOICE_ID
        chat_settings[chat_id].pop("revoice_voice_name", None)
        save_chat_settings()
        return "🎲 Рандом"

    voices = await get_revoice_voices()
    selected = next(
        (voice for voice in voices if voice.voice_id == normalized_id),
        None,
    )
    if selected is None:
        raise ValueError("Voice is no longer available")

    chat_settings[chat_id]["revoice_voice_id"] = selected.voice_id
    chat_settings[chat_id]["revoice_voice_name"] = selected.name
    save_chat_settings()
    return selected.name


__all__ = [
    "REVOICE_VOICES_PAGE_SIZE",
    "get_revoice_selection_label",
    "get_revoice_settings_markup",
    "set_revoice_voice",
]
