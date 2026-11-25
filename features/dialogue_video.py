"""Capture recent group exchanges and deliver a replay to the originating topic."""

from collections import OrderedDict, deque
from datetime import timedelta
import logging
from pathlib import Path
import random
import re
from tempfile import TemporaryDirectory
from time import monotonic

from aiogram import BaseMiddleware
from aiogram.types import FSInputFile, Message

from core.state import chat_settings
from services.dialogue_video import HEIGHT, WIDTH, Line, render_dialogue_video


TEXT_COMMAND = re.compile(r"\s*(?:упупа[\s,]+)?диалог\s*", re.IGNORECASE)
PROBABILITY_KEY = "dialogue_video_prob"
DEFAULT_PROBABILITY = 0.001
MANUAL_COOLDOWN = 60
RANDOM_COOLDOWN = 3600
_FRESHNESS = timedelta(minutes=30)
_MAX_CONTEXTS = 512
_LINES = 7
_GROUPS = {"group", "supergroup"}
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u202a-\u202e\u2066-\u2069]")
_history: OrderedDict[tuple[int, int | None], deque[Line]] = OrderedDict()
_last_video: OrderedDict[int, float] = OrderedDict()
_active_chats: set[int] = set()
_logger = logging.getLogger(__name__)


def _topic(message: Message) -> tuple[int, int | None]:
    return message.chat.id, message.message_thread_id if getattr(message, "is_topic_message", False) else None


def _clean(text: str, limit: int) -> str:
    text = " ".join(_CONTROL_CHARS.sub("", text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _record(message: Message) -> None:
    user = message.from_user
    if (
        message.chat.type not in _GROUPS
        or not user or user.is_bot or message.sender_chat
        or message.has_protected_content or message.chat.has_protected_content
        or not message.text or message.text.lstrip().startswith("/")
        or TEXT_COMMAND.fullmatch(message.text)
    ):
        return
    text = _clean(message.text, 140)
    if not text:
        return
    key = _topic(message)
    history = _history.setdefault(key, deque(maxlen=_LINES))
    _history.move_to_end(key)
    if len(_history) > _MAX_CONTEXTS:
        _history.popitem(last=False)
    if any(line.message_id == message.message_id for line in history):
        return
    history.append(Line(message.message_id, user.id, _clean(user.full_name, 45) or "Участник", text, message.date))


class DialogueVideoHistoryMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        _record(event)
        return await handler(event, data)


async def send_dialogue_video(message: Message, *, automatic: bool = False) -> bool:
    chat_id = message.chat.id
    if message.chat.type not in _GROUPS:
        if not automatic:
            await message.answer("Команда «диалог» работает в групповых чатах.")
        return False
    if getattr(message, "has_protected_content", False) or getattr(message.chat, "has_protected_content", False):
        if not automatic:
            await message.answer("В этом чате включена защита содержимого.")
        return False
    if automatic and not message.text:
        return False

    lines = sorted(
        (
            line for line in _history.get(_topic(message), ())
            if message.date - _FRESHNESS <= line.date <= message.date
            and (line.message_id < message.message_id or (automatic and line.message_id == message.message_id))
        ),
        key=lambda line: line.message_id,
    )
    if automatic and (len(lines) < 4 or lines[-1].message_id != message.message_id):
        return False
    if len({line.author_id for line in lines}) < 2:
        if not automatic:
            await message.answer("За последние 30 минут здесь нужно хотя бы по одной реплике от двух участников.")
        return False

    if automatic:
        probability = chat_settings.get(str(chat_id), {}).get(PROBABILITY_KEY, DEFAULT_PROBABILITY)
        if random.random() >= probability:
            return False
    cooldown = RANDOM_COOLDOWN if automatic else MANUAL_COOLDOWN
    last_video = _last_video.get(chat_id)
    cooling_down = last_video is not None and monotonic() - last_video < cooldown
    if chat_id in _active_chats or len(_active_chats) >= 2 or cooling_down:
        if not automatic:
            await message.answer("Видео недавно отправлено или ещё готовится. Попробуй чуть позже.")
        return False

    _active_chats.add(chat_id)
    try:
        with TemporaryDirectory(prefix="upupa-dialogue-") as directory:
            video = await render_dialogue_video(lines, Path(directory))
            await message.answer_video(
                FSInputFile(video),
                width=WIDTH,
                height=HEIGHT,
                supports_streaming=True,
                protect_content=True,
            )
        _last_video[chat_id] = monotonic()
        _last_video.move_to_end(chat_id)
        if len(_last_video) > _MAX_CONTEXTS:
            _last_video.popitem(last=False)
        return True
    except Exception:
        _logger.exception("Dialogue video failed for chat=%s", chat_id)
        if not automatic:
            await message.answer("Не получилось собрать видео. Попробуй чуть позже.")
        return False
    finally:
        _active_chats.discard(chat_id)
