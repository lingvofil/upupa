"""Telegram transport for the on-demand Radio Upupa feature."""

from __future__ import annotations

import logging
import re

from aiogram import Router, types
from aiogram.types import BufferedInputFile

from core.settings import BLOCKED_USERS
from core.state import chat_settings
from core.upupa_utils import normalize_upupa_command
from features.radio.service import RadioHistoryError, build_radio_episode
from services.speech import SpeechSynthesisError


router = Router(name="radio")

_RADIO_COMMAND_RE = re.compile(r"^(?:радио упупы|радио упупа|упупа радио)$")


def is_radio_command(text: str | None) -> bool:
    normalized = normalize_upupa_command(text or "")
    return bool(_RADIO_COMMAND_RE.fullmatch(normalized))


def is_radio_enabled(chat_id: int | str) -> bool:
    return chat_settings.get(str(chat_id), {}).get("radio_enabled", True)


async def _deliver_radio(
    *,
    bot,
    chat_id: int,
    reply_to_message_id: int | None,
    status,
) -> None:
    await bot.send_chat_action(chat_id=chat_id, action="record_voice")

    try:
        episode = await build_radio_episode(str(chat_id))
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
            episode.requested_duration_minutes,
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

    status = await message.reply("захажу в радиорубку")
    await _deliver_radio(
        bot=message.bot,
        chat_id=message.chat.id,
        reply_to_message_id=message.message_id,
        status=status,
    )
