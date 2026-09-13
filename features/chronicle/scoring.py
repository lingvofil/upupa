"""Cheap local scoring and dedup helpers for Chronicle."""

from __future__ import annotations

import re
from collections import deque
from datetime import datetime, timedelta
from typing import Iterable

from features.chronicle import config
from features.chronicle.models import ChronicleEvent


_TRIGGER_RE = re.compile(
    r"\b(обеща(?:ю|л|ла|ли)|спорим|пари|договорил(?:ись|ся)|решил(?:и|а)?|"
    r"выиграл(?:а|и)?|проиграл(?:а|и)?|рекорд|никогда больше|клянусь|"
    r"призн(?:аюсь|ался|алась)|официально)\b",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[\wёЁ]{3,}", re.UNICODE)
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]", re.UNICODE)
_activity: dict[int, deque[tuple[datetime, int]]] = {}


def text_signal(text: str) -> float:
    """Content can open a candidate, but can never make one legendary alone."""
    if not text:
        return 0.0
    score = 1.6 if _TRIGGER_RE.search(text) else 0.0
    if len(text.strip()) >= 220:
        score += 0.25
    score += min(0.35, len(_EMOJI_RE.findall(text)) * config.EMOJI_TEXT_WEIGHT)
    return score


def activity_signal(chat_id: int, user_id: int, when: datetime) -> tuple[float, int, int]:
    bucket = _activity.setdefault(int(chat_id), deque())
    cutoff = when - timedelta(minutes=3)
    while bucket and bucket[0][0] < cutoff:
        bucket.popleft()
    bucket.append((when, int(user_id)))
    count = len(bucket)
    people = len({item[1] for item in bucket})
    if count >= 18 and people >= 4:
        return config.BURST_WEIGHT, count, people
    if count >= 10 and people >= 3:
        return config.BURST_WEIGHT * 0.5, count, people
    return 0.0, count, people


def reaction_score(total: int, unique: int, median: float, p90: float) -> float:
    score = min(6.0, max(0, total) * config.REACTION_WEIGHT)
    score += min(2.0, max(0, unique) * config.UNIQUE_REACTOR_WEIGHT)
    if total > 0 and p90 > 0 and total >= p90:
        score += 1.5
    elif total >= max(3.0, median * 2.0):
        score += 0.8
    elif median > 0 and total > median:
        score += 0.35
    return score


def participant_bonus(count: int) -> float:
    return max(0, min(4, int(count) - 1)) * config.PARTICIPANT_WEIGHT


def token_set(*parts: str) -> set[str]:
    return {token.casefold() for part in parts for token in _TOKEN_RE.findall(part or "")}


def similarity(title: str, summary: str, event: ChronicleEvent) -> float:
    left = token_set(title, summary)
    right = token_set(event.title, event.summary, " ".join(event.keywords))
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def find_duplicate_or_related(
    title: str,
    summary: str,
    participant_ids: Iterable[int],
    recent: list[ChronicleEvent],
) -> tuple[ChronicleEvent | None, list[str]]:
    participant_ids = set(int(item) for item in participant_ids)
    related: list[tuple[float, str]] = []
    for event in recent:
        lexical = similarity(title, summary, event)
        overlap = bool(participant_ids & set(event.participant_ids))
        if lexical >= 0.58 and overlap:
            return event, []
        if lexical >= 0.25 and overlap:
            related.append((lexical, event.id))
    related.sort(reverse=True)
    return None, [event_id for _score, event_id in related[:3]]
