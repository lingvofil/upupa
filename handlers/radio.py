"""Telegram transport for the on-demand Radio Upupa feature."""

from __future__ import annotations

import logging
import re

from aiogram import Router, types
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings import BLOCKED_USERS
from core.state import chat_settings
from core.upupa_utils import normalize_upupa_command
from features.radio.script import RADIO_DURATION_MINUTES
from features.radio.service import RadioHistoryError, build_radio_episode
from services.speech import SpeechSynthesisError


router = Router(name="radio")

_RADIO_COMMAND_RE = re.compile(r"^(?:радио упупы|упупа радио)(?:\s+(\d+))?$")
_RADIO_CALLBACK_PREFIX = "radio:duration:"


def parse_radio_request(text: str | None) -> tuple[bool, int | None]:
    normalized = normalize_upupa_command(text or "")
    match = _RADIO_COMMAND_RE.fullmatch(normalized)
    if not match:
        return False, None
    raw_duration = match.group(1)
    return True, int(raw_duration) if raw_duration is not None else None


def is_radio_command(text: str | None) -> bool:
    matched, _duration = parse_radio_request(text)
    return matched


def is_radio_enabled(chat_id: int | str) -> bool:
    return chat_settings.get(str(chat_id), {}).get("radio_enabled", True)


def get_radio_duration_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for duration in RADIO_DURATION_MINUTES:
        builder.button(text=f"{duration} мин", callback_data=f"{_RADIO_CALLBACK_PREFIX}{duration}")
    builder.adjust(len(RADIO_DURATION_MINUTES))
    return builder.as_markup()


async def _deliver_radio(
    *,
    bot,
    chat_id: int,
    reply_to_message_id: int | None,
    status,
    duration_minutes: int,
) -> None:
    await bot.send_chat_action(chat_id=chat_id, action="record_voice")

    try:
        episode = await build_radio_episode(str(chat_id), duration_minutes=duration_minutes)
    except RadioHistoryError:
        await status.edit_text("📻 Для выпуска пока слишком мало истории. Поговорите ещё чего-нибудь.")
        return
    except SpeechSynthesisError:
        await status.edit_text("📻 Текст выпуска есть, а диктор сегодня охрип. Попробуй позже.")
        return
    except Exception:
        logging.exception("[radio][script] episode build failed chat=%s", chat_id)
        await status.edit_text("📻 Выпуск развалился при подготовке. Попробуй позже.")
        return

    try:
        await status.edit_text("включаю мекрофон")
        logging.info(
            "[radio][telegram_send] chat=%s requested_minutes=%s messages=%s words=%s provider=%s chunks=%s",
            chat_id,
            duration_minutes,
            episode.message_count,
            episode.word_count,
            episode.tts_provider,
            episode.tts_chunks,
        )
        await bot.send_voice(
            chat_id=chat_id,
            voice=BufferedInputFile(episode.audio, filename="upupa-radio.mp3"),
            reply_to_message_id=reply_to_message_id,
        )
    except Exception:
        logging.exception("[radio][telegram_send] failed chat=%s", chat_id)
        await status.edit_text("📻 Выпуск записан, но Telegram отказался его принимать.")
        return

    try:
        await status.delete()
    except Exception:
        pass


@router.message(
    lambda message: bool(message.text)
    and is_radio_command(message.text)
    and message.from_user.id not in BLOCKED_USERS
)
async def handle_radio_command(message: types.Message):
    chat_id = str(message.chat.id)
    if not is_radio_enabled(chat_id):
        await message.reply("📻 Радио Упупы отключено администраторами этого чата.")
        return

    _matched, duration_minutes = parse_radio_request(message.text)
    if duration_minutes is None:
        await message.reply("📻 Скока вещаем?", reply_markup=get_radio_duration_markup())
        return
    if duration_minutes not in RADIO_DURATION_MINUTES:
        choices = ", ".join(str(duration) for duration in RADIO_DURATION_MINUTES)
        await message.reply(f"📻 Можно выбрать только {choices} минут.")
        return

    status = await message.reply("захажу в радиорубку")
    await _deliver_radio(
        bot=message.bot,
        chat_id=message.chat.id,
        reply_to_message_id=message.message_id,
        status=status,
        duration_minutes=duration_minutes,
    )


@router.callback_query(lambda query: bool(query.data) and query.data.startswith(_RADIO_CALLBACK_PREFIX))
async def handle_radio_duration_callback(query: types.CallbackQuery):
    if query.from_user is None or query.from_user.id in BLOCKED_USERS:
        await query.answer()
        return

    try:
        duration_minutes = int((query.data or "").removeprefix(_RADIO_CALLBACK_PREFIX))
    except ValueError:
        await query.answer("Не понял длительность.", show_alert=True)
        return

    if duration_minutes not in RADIO_DURATION_MINUTES:
        await query.answer("Такой длительности нет.", show_alert=True)
        return
    if query.message is None:
        await query.answer()
        return

    chat_id = query.message.chat.id
    if not is_radio_enabled(chat_id):
        await query.answer("Радио Упупы отключено в этом чате.", show_alert=True)
        return

    await query.answer()
    status = query.message
    await status.edit_text("захажу в радиорубку", reply_markup=None)
    replied = getattr(status, "reply_to_message", None)
    reply_to_message_id = getattr(replied, "message_id", None)
    await _deliver_radio(
        bot=query.message.bot,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        status=status,
        duration_minutes=duration_minutes,
    )
