"""Telegram transport for short context-grounded YuE2 songs."""

from __future__ import annotations

import logging
import re

from aiogram import Router, types
from aiogram.types import FSInputFile

from core.settings import BLOCKED_USERS
from features.song.hf_yue2 import (
    Yue2ConfigurationError,
    Yue2GenerationError,
    Yue2QuotaError,
)
from features.song.lyrics import SongDraftError
from features.song.service import (
    GeneratedSong,
    SongHistoryError,
    SongTarget,
    SongTargetError,
    build_chat_song,
    build_person_song,
)


router = Router(name="song")
_USERNAME_RE = re.compile(r"^песня\s+@([A-Za-z0-9_]{1,64})$", re.IGNORECASE)


def _normalized_text(message: types.Message) -> str:
    return re.sub(r"\s+", " ", (message.text or "").strip())


def _text_mention_users(message: types.Message) -> list:
    result = []
    for entity in message.entities or []:
        entity_type = str(entity.type).lower()
        if entity_type.endswith("text_mention") and entity.user and not entity.user.is_bot:
            result.append(entity.user)
    return result


def parse_song_request(message: types.Message) -> tuple[str | None, SongTarget | None]:
    text = _normalized_text(message)
    if text.casefold() == "песня чат":
        return "chat", None

    match = _USERNAME_RE.fullmatch(text)
    if match:
        return "person", SongTarget(username=match.group(1))

    if text.casefold().startswith("песня "):
        mentioned = _text_mention_users(message)
        if len(mentioned) == 1:
            user = mentioned[0]
            return "person", SongTarget(
                user_id=user.id,
                username=getattr(user, "username", None),
                display_name=getattr(user, "full_name", None) or getattr(user, "first_name", None),
            )
    return None, None


def is_song_command(message: types.Message) -> bool:
    mode, _target = parse_song_request(message)
    return mode is not None


def _quota_message(exc: Yue2QuotaError) -> str:
    if exc.retry_hint:
        return (
            "🎵 GPU-квота Hugging Face на сегодня решила, что концертов хватит. "
            f"По ответу сервиса следующая попытка: {exc.retry_hint}."
        )
    return "🎵 GPU-квота Hugging Face кончилась. Попробуй позже, когда она отрастёт обратно."


async def _send_song(message: types.Message, status, song: GeneratedSong) -> None:
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action="upload_voice")
        await message.bot.send_audio(
            chat_id=message.chat.id,
            audio=FSInputFile(song.mp3_path, filename="upupa-song.mp3"),
            caption=f"🎵 «{song.draft.title}»",
            title=song.draft.title,
            performer="Упупа",
            reply_to_message_id=message.message_id,
        )
    except Exception:
        logging.exception("[song][telegram] failed chat=%s", message.chat.id)
        await status.edit_text("🎵 Песня готова, а Telegram решил не пускать группу на сцену. Попробуй ещё раз.")
        return
    finally:
        try:
            song.mp3_path.unlink(missing_ok=True)
        except Exception:
            logging.exception("[song][cleanup] failed path=%s", song.mp3_path)

    try:
        await status.delete()
    except Exception:
        pass


@router.message(
    lambda message: bool(message.text)
    and is_song_command(message)
    and message.from_user is not None
    and message.from_user.id not in BLOCKED_USERS
)
async def handle_song_command(message: types.Message):
    mode, target = parse_song_request(message)
    if mode is None:
        return

    status = await message.reply("🎵 Пошол собирать группу из людей, которых выгнали из караоке…")
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
        if mode == "chat":
            song = await build_chat_song(str(message.chat.id))
        else:
            if target is None:
                raise SongTargetError("Не понял, про кого петь.")
            song = await build_person_song(str(message.chat.id), target)
    except SongHistoryError:
        await status.edit_text("🎵 Тут пока слишком мало свежей хуйни для содержательной песни. Сначала наговорите материала.")
        return
    except SongTargetError as exc:
        await status.edit_text(f"🎵 {exc}")
        return
    except SongDraftError:
        logging.exception("[song][lyrics] composition failed chat=%s", message.chat.id)
        await status.edit_text("🎵 Текстовик нажрался и не смог уложиться в восемь строк. Попробуй ещё раз.")
        return
    except Yue2QuotaError as exc:
        await status.edit_text(_quota_message(exc))
        return
    except Yue2ConfigurationError:
        logging.exception("[song][yue2] private Space auth/config failed chat=%s", message.chat.id)
        await status.edit_text("🎵 Не могу попасть в приватную студию YuE2. Админу надо проверить HF_TOKEN и доступ к Space.")
        return
    except Yue2GenerationError:
        logging.exception("[song][yue2] generation failed chat=%s", message.chat.id)
        await status.edit_text("🎵 YuE2 сейчас развалил аппаратуру. Попробуй ещё раз позже.")
        return
    except Exception:
        logging.exception("[song] unexpected failure chat=%s", message.chat.id)
        await status.edit_text("🎵 Группа распалась прямо во время саундчека. Попробуй ещё раз.")
        return

    await _send_song(message, status, song)
