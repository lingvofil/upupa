"""Application service for on-demand Radio Upupa episodes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from AI.summarize import _get_chat_messages
from core.paths import USER_MESSAGES_LOG_PATH
from features.radio.script import RadioScript, generate_radio_script
from services.speech import SpeechAudio, SpeechSynthesisError, synthesize_speech


HISTORY_WINDOWS_HOURS = (24, 72, 168)
MIN_MESSAGES = 6
MIN_TEXT_CHARS = 240
RADIO_HISTORY_SAMPLE_MESSAGES = 5000
RADIO_HISTORY_RECENT_MESSAGES = 1000


@dataclass(frozen=True)
class RadioEpisode:
    audio: bytes
    script: str
    word_count: int
    estimated_seconds: int
    period_hours: int
    message_count: int
    tts_provider: str
    tts_chunks: int


class RadioHistoryError(RuntimeError):
    pass


async def collect_radio_history(
    chat_id: str,
    *,
    log_file_path: str | Path = USER_MESSAGES_LOG_PATH,
    now: datetime | None = None,
) -> tuple[list[dict], str | None, int]:
    """Collect 24h of chat history, expanding to 3/7 days only when needed."""
    now = now or datetime.now()
    latest_messages: list[dict] = []
    latest_chat_name: str | None = None

    for period_hours in HISTORY_WINDOWS_HOURS:
        threshold = now - timedelta(hours=period_hours)
        logging.info("[radio][collect] chat=%s period_hours=%s", chat_id, period_hours)
        try:
            messages, _users, chat_name = await asyncio.to_thread(
                _get_chat_messages,
                log_file_path,
                chat_id,
                threshold,
                RADIO_HISTORY_SAMPLE_MESSAGES,
                RADIO_HISTORY_RECENT_MESSAGES,
            )
        except Exception:
            logging.exception(
                "[radio][collect] failed chat=%s period_hours=%s",
                chat_id,
                period_hours,
            )
            raise

        latest_messages = messages
        latest_chat_name = chat_name or latest_chat_name
        text_chars = sum(len(str(message.get("text") or "")) for message in messages)
        logging.info(
            "[radio][collect] chat=%s period_hours=%s messages=%s chars=%s",
            chat_id,
            period_hours,
            len(messages),
            text_chars,
        )
        if len(messages) >= MIN_MESSAGES and text_chars >= MIN_TEXT_CHARS:
            return messages, latest_chat_name, period_hours

    text_chars = sum(len(str(message.get("text") or "")) for message in latest_messages)
    if len(latest_messages) < MIN_MESSAGES or text_chars < MIN_TEXT_CHARS:
        logging.info(
            "[radio][collect] insufficient history chat=%s messages=%s chars=%s",
            chat_id,
            len(latest_messages),
            text_chars,
        )
        raise RadioHistoryError("Недостаточно сообщений для содержательного выпуска")

    return latest_messages, latest_chat_name, HISTORY_WINDOWS_HOURS[-1]


async def _world_radio_context(chat_id: str) -> str | None:
    """Attach world facts only for chats participating in the World of Upupa."""
    try:
        from features.world.news import build_world_radio_context
        from features.world.service import get_world_service

        service = get_world_service()
        if not await service.is_enabled(int(chat_id)):
            return None
        return await build_world_radio_context(service)
    except Exception:
        logging.exception("[radio][world] failed to build world context chat=%s", chat_id)
        return None


async def build_radio_episode(
    chat_id: str,
    *,
    log_file_path: str | Path = USER_MESSAGES_LOG_PATH,
    now: datetime | None = None,
) -> RadioEpisode:
    messages, chat_name, period_hours = await collect_radio_history(
        chat_id,
        log_file_path=log_file_path,
        now=now,
    )
    world_context = await _world_radio_context(chat_id)

    try:
        script_result: RadioScript = await generate_radio_script(
            chat_id,
            chat_name,
            messages,
            period_hours,
            world_context=world_context,
        )
    except Exception:
        logging.exception("[radio][script] generation failed chat=%s", chat_id)
        raise

    logging.info(
        "[radio][tts] chat=%s words=%s estimated_seconds=%s",
        chat_id,
        script_result.word_count,
        script_result.estimated_seconds,
    )
    try:
        speech: SpeechAudio = await synthesize_speech(
            script_result.text,
            provider_order=("gemini", "groq"),
            allow_groq_for_cyrillic=False,
        )
    except SpeechSynthesisError:
        logging.exception("[radio][tts] all suitable TTS paths failed chat=%s", chat_id)
        raise

    return RadioEpisode(
        audio=speech.data,
        script=script_result.text,
        word_count=script_result.word_count,
        estimated_seconds=script_result.estimated_seconds,
        period_hours=period_hours,
        message_count=len(messages),
        tts_provider=speech.provider,
        tts_chunks=speech.chunks,
    )