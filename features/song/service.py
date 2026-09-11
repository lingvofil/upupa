"""Application service for context-grounded short songs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from pathlib import Path
import re

from AI.summarize import _get_chat_messages
from core.history_store import get_history_repository
from core.paths import USER_MESSAGES_LOG_PATH
from features.song.hf_yue2 import generate_yue2_song
from features.song.lyrics import SongDraft, generate_song_draft


HISTORY_WINDOWS_HOURS = (24, 72, 168)
SONG_HISTORY_SAMPLE_MESSAGES = 2500
SONG_HISTORY_RECENT_MESSAGES = 700
SONG_CONTEXT_CHARS = 15000
MIN_CHAT_MESSAGES = 5
MIN_CHAT_TEXT_CHARS = 160
MIN_TARGET_MESSAGES = 3
MIN_TARGET_TEXT_CHARS = 55


@dataclass(frozen=True)
class SongTarget:
    user_id: int | None = None
    username: str | None = None
    display_name: str | None = None

    @property
    def label(self) -> str:
        if self.username:
            return f"@{self.username.lstrip('@')}"
        return self.display_name or "участника"


@dataclass(frozen=True)
class GeneratedSong:
    mp3_path: Path
    draft: SongDraft
    period_hours: int
    source_messages: int


class SongHistoryError(RuntimeError):
    pass


class SongTargetError(RuntimeError):
    pass


def _message_name(message: dict) -> str:
    return str(message.get("display_name") or message.get("username") or "Участник").strip()


def _message_text(message: dict) -> str:
    return re.sub(r"\s+", " ", str(message.get("text") or "")).strip()


def _message_line(message: dict) -> str:
    return f"{_message_name(message)}: {_message_text(message)}"


def _cap_lines(messages: list[dict], max_chars: int = SONG_CONTEXT_CHARS) -> str:
    selected: list[str] = []
    used = 0
    for message in reversed(messages):
        text = _message_text(message)
        if not text:
            continue
        line = _message_line(message)[:700]
        extra = len(line) + (1 if selected else 0)
        if selected and used + extra > max_chars:
            break
        selected.append(line)
        used += extra
    return "\n".join(reversed(selected))


async def collect_song_history(
    chat_id: str,
    *,
    log_file_path: str | Path = USER_MESSAGES_LOG_PATH,
    now: datetime | None = None,
) -> tuple[list[dict], dict, str | None, int]:
    """Prefer the last day, expanding only when the chat is too quiet for a real song."""
    now = now or datetime.now()
    latest: tuple[list[dict], dict, str | None] = ([], {}, None)

    for period_hours in HISTORY_WINDOWS_HOURS:
        threshold = now - timedelta(hours=period_hours)
        messages, users, chat_name = await asyncio.to_thread(
            _get_chat_messages,
            str(log_file_path),
            str(chat_id),
            threshold,
            SONG_HISTORY_SAMPLE_MESSAGES,
            SONG_HISTORY_RECENT_MESSAGES,
        )
        latest = (messages, users, chat_name)
        chars = sum(len(_message_text(message)) for message in messages)
        logging.info(
            "[song][history] chat=%s hours=%s messages=%s chars=%s",
            chat_id,
            period_hours,
            len(messages),
            chars,
        )
        if len(messages) >= MIN_CHAT_MESSAGES and chars >= MIN_CHAT_TEXT_CHARS:
            return messages, users, chat_name, period_hours

    messages, users, chat_name = latest
    raise SongHistoryError(
        f"Недостаточно свежей переписки: {len(messages)} сообщений за {HISTORY_WINDOWS_HOURS[-1]} часов"
    )


def _username_matches(message: dict, username: str) -> bool:
    return str(message.get("username") or "").lstrip("@").casefold() == username.lstrip("@").casefold()


def _resolve_known_username(users: dict, username: str) -> tuple[int | None, str] | None:
    wanted = username.lstrip("@").casefold()
    matches: list[tuple[int | None, str]] = []
    for raw_user_id, info in users.items():
        known = str((info or {}).get("username") or "").lstrip("@").casefold()
        if known != wanted:
            continue
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            user_id = None
        display = str((info or {}).get("display_name") or username).strip() or username
        matches.append((user_id, display))
    if len(matches) == 1:
        return matches[0]
    return None


def _target_rows_from_repository(
    chat_id: str,
    target: SongTarget,
    *,
    start_time: datetime,
    log_file_path: str | Path,
) -> list[dict]:
    if target.user_id is None:
        return []
    repository = get_history_repository(log_file_path)
    if repository is None or not hasattr(repository, "select"):
        return []
    rows = repository.select(
        str(chat_id),
        user_id=str(target.user_id),
        start=start_time,
        nonempty=True,
        limit=80,
    )
    return [
        {
            "date": datetime.fromisoformat(row["timestamp"]).strftime("%d.%m"),
            "username": row.get("username") or target.username or "",
            "display_name": (row.get("full_name") or target.display_name or row.get("username") or "Участник").strip(),
            "text": (row.get("text") or "").strip(),
        }
        for row in rows
        if (row.get("text") or "").strip()
    ]


def _neighbor_context(messages: list[dict], target_indexes: list[int], radius: int = 2) -> list[dict]:
    indexes: set[int] = set()
    for index in target_indexes[-30:]:
        for candidate in range(max(0, index - radius), min(len(messages), index + radius + 1)):
            indexes.add(candidate)
    return [messages[index] for index in sorted(indexes)]


def build_person_context(
    chat_id: str,
    messages: list[dict],
    users: dict,
    target: SongTarget,
    *,
    period_hours: int,
    log_file_path: str | Path = USER_MESSAGES_LOG_PATH,
    now: datetime | None = None,
) -> tuple[str, SongTarget, int]:
    """Resolve the requested participant and retain their messages plus nearby chat replies."""
    now = now or datetime.now()
    resolved = target

    if target.username:
        known = _resolve_known_username(users, target.username)
        indexes = [index for index, message in enumerate(messages) if _username_matches(message, target.username)]
        if not indexes and known is None:
            raise SongTargetError(f"Не нашол @{target.username.lstrip('@')} в свежей истории этого чата.")
        if known is not None:
            known_id, display = known
            resolved = SongTarget(
                user_id=target.user_id or known_id,
                username=target.username.lstrip("@"),
                display_name=target.display_name or display,
            )
        target_messages = [messages[index] for index in indexes]
        context_messages = _neighbor_context(messages, indexes)
    else:
        target_messages = _target_rows_from_repository(
            chat_id,
            target,
            start_time=now - timedelta(hours=period_hours),
            log_file_path=log_file_path,
        )
        if not target_messages:
            display = (target.display_name or "").strip()
            if not display:
                raise SongTargetError("Не понял, про кого петь. Нужен @username или Telegram-упоминание.")
            # Legacy journal fallback has no user_id in the public message shape. Only use an exact name.
            indexes = [index for index, message in enumerate(messages) if _message_name(message).casefold() == display.casefold()]
            target_messages = [messages[index] for index in indexes]
            context_messages = _neighbor_context(messages, indexes)
        else:
            # With a real Telegram user_id the target rows are unambiguous; add the freshest chat around them as context.
            context_messages = messages[-40:]

    chars = sum(len(_message_text(message)) for message in target_messages)
    if len(target_messages) < MIN_TARGET_MESSAGES or chars < MIN_TARGET_TEXT_CHARS:
        raise SongTargetError(
            f"Про {resolved.label} пока слишком мало свежего материала для нормальной песни."
        )

    target_block = _cap_lines(target_messages, max_chars=8000)
    around_block = _cap_lines(context_messages, max_chars=6500)
    context = (
        f"Целевой участник: {resolved.label}\n"
        "Его/её свежие сообщения:\n"
        f"{target_block}\n\n"
        "Контекст вокруг этих реплик и свежая жизнь чата:\n"
        f"{around_block}"
    )
    return context[:SONG_CONTEXT_CHARS], resolved, len(target_messages)


async def build_chat_song(
    chat_id: str,
    *,
    log_file_path: str | Path = USER_MESSAGES_LOG_PATH,
    now: datetime | None = None,
) -> GeneratedSong:
    messages, _users, chat_name, period_hours = await collect_song_history(
        chat_id,
        log_file_path=log_file_path,
        now=now,
    )
    context = _cap_lines(messages)
    draft = await generate_song_draft(
        str(chat_id),
        source_context=context,
        subject=chat_name or f"чат {chat_id}",
        mode="chat",
    )
    path = await generate_yue2_song(draft.lyrics, draft.style_prompt)
    return GeneratedSong(path, draft, period_hours, len(messages))


async def build_person_song(
    chat_id: str,
    target: SongTarget,
    *,
    log_file_path: str | Path = USER_MESSAGES_LOG_PATH,
    now: datetime | None = None,
) -> GeneratedSong:
    current_now = now or datetime.now()
    messages, users, _chat_name, period_hours = await collect_song_history(
        chat_id,
        log_file_path=log_file_path,
        now=current_now,
    )
    context, resolved, target_count = build_person_context(
        str(chat_id),
        messages,
        users,
        target,
        period_hours=period_hours,
        log_file_path=log_file_path,
        now=current_now,
    )
    draft = await generate_song_draft(
        str(chat_id),
        source_context=context,
        subject=resolved.label,
        mode="person",
    )
    path = await generate_yue2_song(draft.lyrics, draft.style_prompt)
    return GeneratedSong(path, draft, period_hours, target_count)
