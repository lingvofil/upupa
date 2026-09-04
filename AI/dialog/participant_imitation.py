"""Bounded participant identity, profile refresh and semantic-memory helpers."""

from __future__ import annotations

import random
import re
from collections import deque
from datetime import datetime, timedelta, timezone

import aiofiles

from AI.dialog.style import create_user_style_prompt, is_participant_style_message
from core.paths import USER_MESSAGES_LOG_PATH as LOG_FILE
from services.smart_search import find_relevant_context


PARTICIPANT_TURN_SAMPLE_SIZE = 200
PARTICIPANT_TURN_RECENT_SIZE = 100
PROFILE_REFRESH_MESSAGE_DELTA = 50
PROFILE_REFRESH_MAX_AGE = timedelta(days=3)


def _log_pattern(chat_id: int | str) -> re.Pattern:
    return re.compile(
        rf".* - Chat {re.escape(str(chat_id))}\b.*User (?P<user_id>\d+) "
        r"\((?P<username>[^)]+)\) \[(?P<full_name>.+?)\]: (?P<text>.*)"
    )


def _normalize_optional(value: str, sentinel: str) -> str | None:
    value = value.strip()
    return None if not value or value == sentinel else value


async def resolve_participant_identity(query: str, chat_id: int | str) -> dict | None:
    """Resolve a username/full name once and pin imitation to stable Telegram user_id."""
    target = query.strip().lstrip("@").casefold()
    if not target:
        return None

    username_hits: dict[int, dict] = {}
    full_name_hits: dict[int, dict] = {}
    sequence = 0
    pattern = _log_pattern(chat_id)

    async with aiofiles.open(LOG_FILE, mode="r", encoding="utf-8") as file:
        async for line in file:
            match = pattern.match(line)
            if not match:
                continue
            sequence += 1
            user_id = int(match.group("user_id"))
            username = _normalize_optional(match.group("username"), "NoUsername")
            full_name = _normalize_optional(match.group("full_name"), "NoName")

            bucket = None
            if username and username.casefold() == target:
                bucket = username_hits
            elif full_name and full_name.casefold() == target:
                bucket = full_name_hits
            elif target.isdigit() and int(target) == user_id:
                bucket = username_hits

            if bucket is None:
                continue

            stats = bucket.setdefault(
                user_id,
                {
                    "user_id": user_id,
                    "username": username,
                    "full_name": full_name,
                    "message_count": 0,
                    "last_sequence": sequence,
                },
            )
            stats["username"] = username or stats.get("username")
            stats["full_name"] = full_name or stats.get("full_name")
            stats["message_count"] += 1
            stats["last_sequence"] = sequence

    hits = username_hits or full_name_hits
    if not hits:
        return None

    # username is normally unique; for ambiguous full names choose the most
    # represented, most recently seen account and then pin its immutable id.
    best = max(hits.values(), key=lambda item: (item["message_count"], item["last_sequence"]))
    return {
        "user_id": best["user_id"],
        "username": best.get("username"),
        "full_name": best.get("full_name"),
        "display_name": best.get("full_name") or best.get("username") or str(best["user_id"]),
    }


def _reservoir_add(reservoir: list[tuple[int, str]], item: tuple[int, str], seen: int, capacity: int) -> None:
    if capacity <= 0:
        return
    if len(reservoir) < capacity:
        reservoir.append(item)
        return
    replacement_index = random.randrange(seen)
    if replacement_index < capacity:
        reservoir[replacement_index] = item


async def sample_participant_messages(
    user_id: int,
    chat_id: int | str,
    *,
    sample_size: int = PARTICIPANT_TURN_SAMPLE_SIZE,
    recent_size: int = PARTICIPANT_TURN_RECENT_SIZE,
) -> tuple[list[str], int]:
    """Stream one user's history into bounded recent + reservoir samples.

    The complete history is never materialized in RAM. The returned count is
    used to decide whether the style profile needs refreshing.
    """
    if sample_size <= 0:
        return [], 0

    recent_capacity = min(max(recent_size, 0), sample_size)
    historical_capacity = sample_size - recent_capacity
    recent: deque[tuple[int, str]] = deque(maxlen=recent_capacity or None)
    historical: list[tuple[int, str]] = []
    historical_seen = 0
    matched = 0
    sequence = 0
    pattern = _log_pattern(chat_id)

    async with aiofiles.open(LOG_FILE, mode="r", encoding="utf-8") as file:
        async for line in file:
            match = pattern.match(line)
            if not match or int(match.group("user_id")) != int(user_id):
                continue

            matched += 1
            text = match.group("text").strip()
            if not text:
                continue
            sequence += 1

            if recent_capacity:
                if len(recent) == recent_capacity:
                    displaced = recent.popleft()
                    historical_seen += 1
                    _reservoir_add(historical, displaced, historical_seen, historical_capacity)
                recent.append((sequence, text))
            else:
                historical_seen += 1
                _reservoir_add(historical, (sequence, text), historical_seen, historical_capacity)

    historical.sort(key=lambda item: item[0])
    return [text for _index, text in historical] + [text for _index, text in recent], matched


def _profile_needs_refresh(settings: dict, message_count: int) -> bool:
    if not settings.get("prompt"):
        return True

    previous_count = settings.get("style_profile_message_count")
    if not isinstance(previous_count, int) or message_count >= previous_count + PROFILE_REFRESH_MESSAGE_DELTA:
        return True

    updated_at = settings.get("style_profile_updated_at")
    if not updated_at:
        return True
    try:
        parsed = datetime.fromisoformat(updated_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True

    return datetime.now(timezone.utc) - parsed >= PROFILE_REFRESH_MAX_AGE


def refresh_style_profile(settings: dict, messages: list[str], message_count: int) -> bool:
    """Refresh the cached prompt when the participant has materially evolved."""
    identity = settings.get("imitated_user", {})
    display_name = identity.get("display_name") or settings.get("prompt_name") or "участник"
    usable = [message for message in messages if is_participant_style_message(message)]
    if not usable or not _profile_needs_refresh(settings, message_count):
        return False

    settings["prompt"] = create_user_style_prompt(usable, display_name)
    settings["style_profile_message_count"] = message_count
    settings["style_profile_updated_at"] = datetime.now(timezone.utc).isoformat()
    return True


async def initialize_participant_profile(
    chat_id: int | str,
    query: str,
    settings: dict,
) -> dict | None:
    """Resolve a requested participant, pin user_id and build the initial profile."""
    identity = await resolve_participant_identity(query, chat_id)
    if not identity:
        return None

    messages, message_count = await sample_participant_messages(identity["user_id"], chat_id)
    usable = [message for message in messages if is_participant_style_message(message)]
    if not usable:
        return None

    settings["prompt"] = create_user_style_prompt(usable, identity["display_name"])
    settings["prompt_name"] = identity["display_name"]
    settings["prompt_source"] = "user_imitation"
    settings["prompt_type"] = "user_style"
    settings["imitated_user"] = identity
    settings["style_profile_message_count"] = message_count
    settings["style_profile_updated_at"] = datetime.now(timezone.utc).isoformat()
    return identity


async def prepare_participant_turn(chat_id: int | str, settings: dict, query_text: str) -> tuple[str, bool]:
    """Return semantic memory for a turn and refresh/migrate the style profile."""
    identity = settings.get("imitated_user", {})
    changed = False

    user_id = identity.get("user_id")
    if not user_id:
        legacy_name = identity.get("username") or identity.get("full_name") or identity.get("display_name")
        if legacy_name:
            resolved = await resolve_participant_identity(legacy_name, chat_id)
            if resolved:
                settings["imitated_user"] = resolved
                identity = resolved
                user_id = resolved["user_id"]
                changed = True

    if not user_id:
        return (
            "\n\n[SEMANTIC MEMORY]\nНет надёжно идентифицированной памяти участника. "
            "Имитируй только стиль и не выдумывай его взгляды, опыт или предпочтения.\n[/SEMANTIC MEMORY]",
            changed,
        )

    messages, message_count = await sample_participant_messages(int(user_id), chat_id)
    if refresh_style_profile(settings, messages, message_count):
        changed = True

    relevant_messages = await find_relevant_context(query_text, messages, top_k=3)
    if not relevant_messages:
        return (
            "\n\n[SEMANTIC MEMORY]\nПо текущей теме нет достаточно близких старых сообщений участника. "
            "Сохраняй его манеру речи, но не приписывай ему конкретные убеждения, факты биографии, "
            "предпочтения или прошлый опыт.\n[/SEMANTIC MEMORY]",
            changed,
        )

    memory_lines = "\n".join(f"- {message}" for message in relevant_messages)
    return (
        "\n\n[SEMANTIC MEMORY]\n"
        "Ниже — реальные старые сообщения участника на похожую тему. Используй их только как основание для "
        "его возможной позиции и контекста. Не цитируй их дословно и не копируй характерные фразы механически.\n"
        f"{memory_lines}\n"
        "[/SEMANTIC MEMORY]",
        changed,
    )
