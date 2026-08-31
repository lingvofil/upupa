"""Selection of fresh, anonymized chat episodes for autonomous channel posts."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timedelta
from typing import Callable, Iterable

from core.state import chat_list

CHAT_LOOKBACK_HOURS = 24
CHAT_EPISODE_GAP_MINUTES = 12
CHAT_EPISODE_MIN_MESSAGES = 5
CHAT_EPISODE_MAX_MESSAGES = 25
CHAT_EPISODE_MIN_PARTICIPANTS = 2
CHAT_EPISODE_MIN_SUBSTANTIVE_MESSAGES = 3
CHAT_SUBSTANTIVE_MIN_CHARS = 8
CHAT_GUARANTEE_AFTER_RECENT_POSTS = 3
CHAT_GUARANTEE_WINDOW_HOURS = 24
CHAT_USED_EPISODE_WINDOW_HOURS = 36

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{4,}")


def _sanitize_chat_text(text: str) -> str:
    text = re.sub(r"@[A-Za-z0-9_]{3,}", "@пользователь", text)
    text = re.sub(r"https?://\S+", "[ссылка]", text)
    text = re.sub(r"\b\d{7,}\b", "[номер]", text)
    return text.strip()


def anonymize_fragment(messages: list[dict]) -> str:
    aliases: dict[str, str] = {}
    lines: list[str] = []
    for message in messages:
        raw_name = str(message.get("name") or "участник")
        if raw_name not in aliases:
            aliases[raw_name] = f"Участник {len(aliases) + 1}"
        text = _sanitize_chat_text(str(message.get("text") or ""))
        if text:
            lines.append(f"{aliases[raw_name]}: {text}")
    return "\n".join(lines)


def _normalize_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _now_naive(now: datetime | None = None) -> datetime:
    current = now or datetime.now().astimezone()
    if current.tzinfo is not None:
        return current.astimezone().replace(tzinfo=None)
    return current


def _split_episodes(messages: list[dict]) -> list[list[dict]]:
    episodes: list[list[dict]] = []
    current: list[dict] = []
    previous_dt: datetime | None = None
    max_gap = timedelta(minutes=CHAT_EPISODE_GAP_MINUTES)

    for message in messages:
        dt = _normalize_dt(message.get("dt"))
        text = str(message.get("text") or "").strip()
        if dt is None or not text:
            continue
        normalized = {**message, "dt": dt, "text": text}
        if previous_dt is not None and dt - previous_dt > max_gap:
            if current:
                episodes.append(current)
            current = []
        current.append(normalized)
        previous_dt = dt

    if current:
        episodes.append(current)
    return episodes


def _substantive_count(messages: list[dict]) -> int:
    return sum(
        1
        for message in messages
        if len(str(message.get("text") or "").strip()) >= CHAT_SUBSTANTIVE_MIN_CHARS
    )


def _topic_repetition_score(messages: list[dict]) -> int:
    seen_by_message: list[set[str]] = []
    for message in messages:
        tokens = {token.casefold() for token in _TOKEN_RE.findall(str(message.get("text") or ""))}
        seen_by_message.append(tokens)

    counts: dict[str, int] = {}
    for tokens in seen_by_message:
        for token in tokens:
            counts[token] = counts.get(token, 0) + 1
    return sum(1 for count in counts.values() if count >= 2)


def _episode_score(messages: list[dict], *, now: datetime) -> float:
    latest = _normalize_dt(messages[-1].get("dt")) or now
    age_hours = max(0.0, (now - latest).total_seconds() / 3600)
    recency = max(0.0, CHAT_LOOKBACK_HOURS - age_hours)
    participants = len({str(message.get("name") or "участник") for message in messages})
    substantive = _substantive_count(messages)
    questions = sum("?" in str(message.get("text") or "") for message in messages)
    repetition = min(_topic_repetition_score(messages), 8)

    return (
        recency * 2.0
        + min(len(messages), CHAT_EPISODE_MAX_MESSAGES) * 1.3
        + min(participants, 6) * 3.0
        + substantive * 1.5
        + min(questions, 5) * 1.5
        + repetition * 2.0
    )


def _episode_key(chat_id: int, messages: list[dict]) -> str:
    first = _normalize_dt(messages[0].get("dt"))
    last = _normalize_dt(messages[-1].get("dt"))
    payload = f"{chat_id}:{first.isoformat() if first else ''}:{last.isoformat() if last else ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _qualifies(messages: list[dict]) -> bool:
    participants = {str(message.get("name") or "участник") for message in messages}
    return (
        len(messages) >= CHAT_EPISODE_MIN_MESSAGES
        and len(participants) >= CHAT_EPISODE_MIN_PARTICIPANTS
        and _substantive_count(messages) >= CHAT_EPISODE_MIN_SUBSTANTIVE_MESSAGES
    )


def _recent_used_keys(published_posts: list[dict], *, now: datetime) -> set[str]:
    cutoff = now - timedelta(hours=CHAT_USED_EPISODE_WINDOW_HOURS)
    keys: set[str] = set()
    for post in published_posts:
        created_at = _normalize_dt(post.get("created_at"))
        if created_at is None or created_at < cutoff:
            continue
        key = str(post.get("chat_context_key") or "").strip()
        if key:
            keys.add(key)
    return keys


def pick_chat_episode(
    published_posts: list[dict],
    *,
    now: datetime | None = None,
    rng=None,
    chats: Iterable[dict] | None = None,
    read_chat_log: Callable[[str], list[dict]] | None = None,
) -> dict | None:
    """Pick one fresh, active multi-user episode and return anonymized material plus metadata."""
    if read_chat_log is None:
        from AI.chat_recall import _read_chat_log

        read_chat_log = _read_chat_log
    if rng is None:
        import random

        rng = random

    current = _now_naive(now)
    cutoff = current - timedelta(hours=CHAT_LOOKBACK_HOURS)
    used_keys = _recent_used_keys(published_posts, now=current)
    candidates: list[dict] = []

    for chat in (chat_list if chats is None else chats):
        try:
            chat_id = int(chat.get("id"))
        except (TypeError, ValueError):
            continue
        if chat_id >= 0:
            continue

        messages = read_chat_log(str(chat_id))
        recent = [
            message
            for message in messages[-500:]
            if (dt := _normalize_dt(message.get("dt"))) is not None and cutoff <= dt <= current
        ]
        for episode in _split_episodes(recent):
            if not _qualifies(episode):
                continue
            trimmed = episode[-CHAT_EPISODE_MAX_MESSAGES:]
            key = _episode_key(chat_id, trimmed)
            if key in used_keys:
                continue
            fragment = anonymize_fragment(trimmed)
            if not fragment:
                continue
            participants = len({str(message.get("name") or "участник") for message in trimmed})
            candidates.append(
                {
                    "key": key,
                    "fragment": fragment,
                    "latest_at": (_normalize_dt(trimmed[-1].get("dt")) or current).isoformat(),
                    "message_count": len(trimmed),
                    "participant_count": participants,
                    "score": _episode_score(trimmed, now=current),
                }
            )

    if not candidates:
        return None

    candidates.sort(key=lambda item: item["score"], reverse=True)
    shortlist = candidates[: min(3, len(candidates))]
    weights = [max(1.0, math.sqrt(float(item["score"]))) for item in shortlist]
    return rng.choices(shortlist, weights=weights, k=1)[0]


def should_force_chat_post(
    published_posts: list[dict],
    *,
    now: datetime | None = None,
) -> bool:
    """After several non-chat posts, reserve the next viable publication for chat context."""
    current = _now_naive(now)
    cutoff = current - timedelta(hours=CHAT_GUARANTEE_WINDOW_HOURS)
    recent: list[dict] = []
    for post in published_posts:
        created_at = _normalize_dt(post.get("created_at"))
        if created_at is not None and cutoff <= created_at <= current:
            recent.append(post)

    if any(bool(post.get("chat_context_used")) for post in recent):
        return False
    return len(recent) >= CHAT_GUARANTEE_AFTER_RECENT_POSTS
