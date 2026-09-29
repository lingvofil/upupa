"""Telegram orchestration for the reply command переозвучь."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import random
import re
import tempfile

from aiogram import Bot, types
from aiogram.types import BufferedInputFile

from core.settings import ELEVENLABS_API_KEY
from infrastructure.media_io import download_telegram_bytes
from services.elevenlabs import (
    ElevenLabsAuthenticationError,
    ElevenLabsClient,
    ElevenLabsConfigurationError,
    ElevenLabsError,
    ElevenLabsInvalidResponseError,
    ElevenLabsNoVoicesError,
    ElevenLabsQuotaError,
    ElevenLabsTemporaryError,
    ElevenLabsTimeoutError,
)


logger = logging.getLogger(__name__)

MAX_REVOICE_SECONDS = 30
FFMPEG_TIMEOUT_SECONDS = 30.0
_REVOICE_COMMAND_RE = re.compile(
    r"^\s*переозвучь(?:\s+(.+?))?\s*$",
    re.IGNORECASE | re.DOTALL,
)

_revoice_semaphore = asyncio.Semaphore(1)
_inflight_updates: set[tuple[int, int]] = set()
_inflight_lock = asyncio.Lock()
_default_client: ElevenLabsClient | None = None
_default_client_key: str | None = None


def parse_revoice_command(text: str | None) -> str | None:
    """Return the optional voice description, or None when this is not the command."""
    if not text:
        return None
    match = _REVOICE_COMMAND_RE.match(text)
    if not match:
        return None
    return (match.group(1) or "").strip()


def prepare_voice_description(description: str) -> str:
    """Satisfy Voice Design's 20-char minimum without changing user intent."""
    normalized = re.sub(r"\s+", " ", description or "").strip()
    if not normalized:
        raise ValueError("Custom voice description is empty")
    if len(normalized) < 20:
        normalized = (
            f"{normalized}. Естественный выразительный голос с указанным характером."
        )
    return normalized[:1000]


async def _run_ffmpeg(command: list[str]) -> tuple[bool, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return False, "ffmpeg timeout"

    if process.returncode != 0:
        return False, stderr.decode(errors="ignore")
    return True, ""


async def trim_voice_to_seconds(
    audio_bytes: bytes,
    *,
    max_seconds: int = MAX_REVOICE_SECONDS,
) -> bytes:
    """Trim Telegram OGG/Opus without re-encoding; re-encode only as fallback."""
    if not audio_bytes:
        raise ValueError("Cannot trim empty voice")

    with tempfile.TemporaryDirectory(prefix="upupa_revoice_") as temp_dir:
        source_path = Path(temp_dir) / "source.ogg"
        trimmed_path = Path(temp_dir) / "trimmed.ogg"
        await asyncio.to_thread(source_path.write_bytes, audio_bytes)

        copy_command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_path),
            "-t",
            str(max_seconds),
            "-map",
            "0:a:0",
            "-c:a",
            "copy",
            "-vn",
            "-y",
            str(trimmed_path),
        ]
        success, error = await _run_ffmpeg(copy_command)

        if not success:
            logger.warning(
                "[revoice] ffmpeg stream-copy trim failed; falling back to opus re-encode: %s",
                error[:300],
            )
            reencode_command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-t",
                str(max_seconds),
                "-map",
                "0:a:0",
                "-c:a",
                "libopus",
                "-b:a",
                "64k",
                "-vn",
                "-y",
                str(trimmed_path),
            ]
            success, error = await _run_ffmpeg(reencode_command)

        if not success or not trimmed_path.exists():
            logger.error("[revoice] ffmpeg trim failed: %s", error[:500])
            raise RuntimeError("Failed to trim voice with ffmpeg")

        result = await asyncio.to_thread(trimmed_path.read_bytes)
        if not result:
            raise RuntimeError("ffmpeg produced empty trimmed voice")
        return result


def _get_default_client() -> ElevenLabsClient:
    global _default_client, _default_client_key

    api_key = str(ELEVENLABS_API_KEY or "").strip()
    if not api_key:
        raise ElevenLabsConfigurationError("ELEVENLABS_API_KEY is not configured")

    if _default_client is None or _default_client_key != api_key:
        _default_client = ElevenLabsClient(api_key)
        _default_client_key = api_key
    return _default_client


def _user_error_message(exc: BaseException) -> str:
    if isinstance(exc, ElevenLabsQuotaError):
        return "У переозвучки закончилась квота. Попробуй позже."
    if isinstance(exc, ElevenLabsNoVoicesError):
        return "Сейчас нет доступных голосов для переозвучки."
    if isinstance(exc, ElevenLabsAuthenticationError):
        return "Переозвучка сейчас недоступна."
    if isinstance(exc, (ElevenLabsTimeoutError, ElevenLabsTemporaryError)):
        return "ElevenLabs сейчас не отвечает. Попробуй позже."
    if isinstance(exc, ElevenLabsInvalidResponseError):
        return "ElevenLabs вернул некорректный ответ. Попробуй позже."
    if isinstance(exc, ElevenLabsConfigurationError):
        return "Переозвучка сейчас недоступна."
    return "Не удалось переозвучить сообщение."


async def _claim_update(chat_id: int, message_id: int) -> bool:
    key = (chat_id, message_id)
    async with _inflight_lock:
        if key in _inflight_updates:
            return False
        _inflight_updates.add(key)
        return True


async def _release_update(chat_id: int, message_id: int) -> None:
    async with _inflight_lock:
        _inflight_updates.discard((chat_id, message_id))


async def handle_revoice_command(
    message: types.Message,
    bot: Bot,
    *,
    client: ElevenLabsClient | None = None,
) -> None:
    """Handle one reply-based ElevenLabs voice-changing request."""
    description = parse_revoice_command(message.text)
    if description is None:
        return

    source = message.reply_to_message
    if source is None:
        await message.reply("Ответь командой «переозвучь» на голосовое сообщение.")
        return
    if not source.voice:
        await message.reply("Переозвучивать пока умею только голосовые сообщения.")
        return

    if client is None and not str(ELEVENLABS_API_KEY or "").strip():
        logger.error("[revoice] ELEVENLABS_API_KEY is not configured")
        await message.reply("Переозвучка сейчас недоступна.")
        return

    chat_id = int(message.chat.id)
    user_id = int(message.from_user.id) if message.from_user else None
    message_id = int(message.message_id)

    if not await _claim_update(chat_id, message_id):
        logger.info(
            "[revoice] duplicate update skipped chat_id=%s user_id=%s message_id=%s",
            chat_id,
            user_id,
            message_id,
        )
        return

    source_duration = getattr(source.voice, "duration", None)
    try:
        source_duration_value = (
            float(source_duration) if source_duration is not None else None
        )
    except (TypeError, ValueError):
        source_duration_value = None

    needs_trim = (
        source_duration_value is None
        or source_duration_value > MAX_REVOICE_SECONDS
    )
    processed_duration = (
        min(source_duration_value, MAX_REVOICE_SECONDS)
        if source_duration_value is not None
        else MAX_REVOICE_SECONDS
    )
    mode = "custom" if description else "random"

    logger.info(
        "[revoice] start chat_id=%s user_id=%s source_duration=%s processed_duration=%s mode=%s prompt_chars=%s",
        chat_id,
        user_id,
        source_duration_value,
        processed_duration,
        mode,
        len(description),
    )

    temporary_voice_id: str | None = None
    resolved_client: ElevenLabsClient | None = None

    try:
        async with _revoice_semaphore:
            audio_bytes = await download_telegram_bytes(
                bot,
                source.voice.file_id,
            )
            if needs_trim:
                audio_bytes = await trim_voice_to_seconds(
                    audio_bytes,
                    max_seconds=MAX_REVOICE_SECONDS,
                )

            resolved_client = client or _get_default_client()
            await resolved_client.ensure_stale_temporary_voice_cleanup()

            if description:
                voice_description = prepare_voice_description(description)
                generated_voice_ids = await resolved_client.design_voice(
                    voice_description
                )
                generated_voice_id = random.choice(generated_voice_ids)
                temporary_voice_id = await resolved_client.create_temporary_voice(
                    generated_voice_id=generated_voice_id,
                    voice_description=voice_description,
                )
                voice_id = temporary_voice_id
                logger.info(
                    "[revoice] temporary voice created chat_id=%s user_id=%s voice_id=%s",
                    chat_id,
                    user_id,
                    voice_id,
                )
            else:
                selected_voice = await resolved_client.choose_random_voice()
                voice_id = selected_voice.voice_id
                logger.info(
                    "[revoice] random voice selected chat_id=%s user_id=%s voice_id=%s",
                    chat_id,
                    user_id,
                    voice_id,
                )

            result = await resolved_client.voice_change(voice_id, audio_bytes)
            logger.info(
                "[revoice] result ready chat_id=%s user_id=%s mode=%s voice_id=%s bytes=%s",
                chat_id,
                user_id,
                mode,
                voice_id,
                len(result),
            )

            await bot.send_voice(
                chat_id=chat_id,
                voice=BufferedInputFile(result, filename="upupa-revoice.mp3"),
                reply_to_message_id=getattr(source, "message_id", None),
            )

    except ElevenLabsError as exc:
        logger.warning(
            "[revoice] ElevenLabs failure chat_id=%s user_id=%s mode=%s error=%s",
            chat_id,
            user_id,
            mode,
            type(exc).__name__,
        )
        await message.reply(_user_error_message(exc))
    except Exception as exc:
        logger.error(
            "[revoice] processing failure chat_id=%s user_id=%s mode=%s error=%s",
            chat_id,
            user_id,
            mode,
            type(exc).__name__,
            exc_info=True,
        )
        await message.reply("Не удалось переозвучить сообщение.")
    finally:
        if temporary_voice_id is not None and resolved_client is not None:
            try:
                await resolved_client.delete_voice(temporary_voice_id)
                logger.info(
                    "[revoice] temporary voice deleted chat_id=%s user_id=%s voice_id=%s",
                    chat_id,
                    user_id,
                    temporary_voice_id,
                )
            except ElevenLabsError as cleanup_exc:
                logger.warning(
                    "[revoice] temporary voice delete failed chat_id=%s user_id=%s voice_id=%s error=%s",
                    chat_id,
                    user_id,
                    temporary_voice_id,
                    type(cleanup_exc).__name__,
                )
            except Exception:
                logger.exception(
                    "[revoice] unexpected temporary voice cleanup failure chat_id=%s user_id=%s voice_id=%s",
                    chat_id,
                    user_id,
                    temporary_voice_id,
                )
        await _release_update(chat_id, message_id)


__all__ = [
    "MAX_REVOICE_SECONDS",
    "handle_revoice_command",
    "parse_revoice_command",
    "prepare_voice_description",
    "trim_voice_to_seconds",
]
