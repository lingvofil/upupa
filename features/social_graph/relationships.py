"""Long-lived relationship layer built on top of deterministic social interactions.

The social graph records facts (reply/mention/reaction).  This module turns the
facts plus grounded Chronicle events into a pair-level relationship view.  Raw
swearing is deliberately *not* used as a conflict signal: tension is raised by
Chronicle events that were classified as conflicts in context.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Iterable

from features.chronicle.models import ChronicleEvent
from features.chronicle.runtime import list_events as list_chronicle_events
from features.social_graph.service import (
    load_relationship_snapshots,
    load_relationship_states,
    save_relationship_snapshot,
)


CHRONICLE_EVENT_LIMIT = 300


@dataclass(frozen=True, slots=True)
class RelationshipView:
    chat_id: int
    user_a_id: int
    user_b_id: int
    user_a_name: str
    user_b_name: str
    affinity: int
    tension: int
    xp: float
    level: int
    reciprocity: float
    archetype: str
    trend: str
    interaction_count: int
    reply_count: int
    mention_count: int
    reaction_count: int
    shared_event_count: int
    a_to_b_count: int
    b_to_a_count: int
    recent_7d: int
    previous_21d: int
    last_interaction_at: datetime | None
    shared_events: tuple[ChronicleEvent, ...]

    @property
    def strength(self) -> float:
        return self.affinity * 0.6 + self.tension * 0.4


@dataclass(frozen=True, slots=True)
class RelationshipSnapshot:
    captured_at: datetime
    affinity: int
    tension: int
    xp: float
    level: int
    reciprocity: float
    archetype: str
    trend: str


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _event_decay(event: ChronicleEvent, now: datetime) -> float:
    age_days = max(0.0, (now - event.event_ended_at).total_seconds() / 86400.0)
    # Landmark events matter for months, but slowly stop dominating the present.
    return 0.35 + 0.65 * math.exp(-age_days / 90.0)


def _pair_events(events: Iterable[ChronicleEvent], user_a_id: int, user_b_id: int) -> tuple[ChronicleEvent, ...]:
    pair = {int(user_a_id), int(user_b_id)}
    return tuple(event for event in events if pair.issubset(set(event.participant_ids)))


def _reciprocity(a_to_b_weight: float, b_to_a_weight: float) -> float:
    high = max(a_to_b_weight, b_to_a_weight)
    if high <= 0:
        return 0.0
    return _clamp(min(a_to_b_weight, b_to_a_weight) / high, 0.0, 1.0)


def _trend(recent_7d: int, previous_21d: int, tension: int, last_interaction_at: datetime | None, now: datetime) -> str:
    baseline_week = previous_21d / 3.0
    if previous_21d == 0 and recent_7d >= 3:
        return "внезапно возобновили общение"
    if recent_7d >= 4 and recent_7d >= max(2.0, baseline_week * 1.6):
        return "конфликт набирает обороты" if tension >= 45 else "резко сближаются"
    if last_interaction_at is not None and (now - last_interaction_at).days >= 14:
        return "исчезли с радаров"
    if baseline_week >= 2 and recent_7d <= baseline_week * 0.45:
        return "остывают"
    return "отношения стабильны"


def _archetype(
    *,
    affinity: int,
    tension: int,
    reciprocity: float,
    interaction_count: int,
    events: tuple[ChronicleEvent, ...],
) -> str:
    categories = [event.category for event in events]
    if interaction_count >= 6 and reciprocity < 0.35:
        return "односторонний сериал"
    if affinity >= 65 and tension >= 50:
        return "заклятые друзья"
    if tension >= 60 and affinity < 50:
        return "взаимная аллергия"
    if categories.count("game") >= 2 and affinity >= 35:
        return "игровые союзники"
    meme_count = sum(category in {"funny", "absurd", "local_meme"} for category in categories)
    if meme_count >= 2 and affinity >= 40:
        return "мемный дуэт"
    if tension >= 35:
        return "спорщики"
    if affinity >= 70:
        return "стабильный дуэт"
    if affinity >= 40:
        return "регулярные собеседники"
    return "периодически пересекаются"


def _calculate_view(chat_id: int, row: dict, events: Iterable[ChronicleEvent], now: datetime) -> RelationshipView:
    user_a_id = int(row["user_a_id"])
    user_b_id = int(row["user_b_id"])
    shared = _pair_events(events, user_a_id, user_b_id)

    a_weight = float(row.get("a_to_b_weight", 0.0) or 0.0)
    b_weight = float(row.get("b_to_a_weight", 0.0) or 0.0)
    reciprocity = _reciprocity(a_weight, b_weight)
    base_xp = float(row.get("xp", 0.0) or 0.0)

    shared_signal = 0.0
    conflict_signal = 0.0
    event_xp = 0.0
    for event in shared:
        decay = _event_decay(event, now)
        importance = max(0.0, float(event.importance_score))
        shared_signal += importance * decay
        event_xp += min(15.0, 2.0 + importance * 0.8)
        if event.category == "conflict":
            conflict_signal += importance * decay

    interaction_count = int(row.get("interaction_count", 0) or 0)
    reply_count = int(row.get("reply_count", 0) or 0)
    mention_count = int(row.get("mention_count", 0) or 0)
    reaction_count = int(row.get("reaction_count", 0) or 0)
    last_interaction_at = _utc(row.get("last_interaction_at"))

    activity = 100.0 * (1.0 - math.exp(-max(0.0, base_xp) / 28.0))
    event_affinity = 100.0 * (1.0 - math.exp(-shared_signal / 32.0))
    variety = sum(value > 0 for value in (reply_count, mention_count, reaction_count)) / 3.0 * 100.0
    affinity = activity * 0.55 + reciprocity * 100.0 * 0.25 + event_affinity * 0.15 + variety * 0.05

    # Recent silence weakens the present-day score but never erases history.
    if last_interaction_at is not None:
        idle_days = max(0.0, (now - last_interaction_at).total_seconds() / 86400.0)
        recency_factor = 0.55 + 0.45 * math.exp(-idle_days / 75.0)
        affinity *= recency_factor
    elif shared:
        latest_event = max(event.event_ended_at for event in shared)
        idle_days = max(0.0, (now - latest_event).total_seconds() / 86400.0)
        affinity *= 0.55 + 0.45 * math.exp(-idle_days / 90.0)

    tension = 100.0 * (1.0 - math.exp(-conflict_signal / 18.0))
    affinity_i = int(round(_clamp(affinity)))
    tension_i = int(round(_clamp(tension)))
    total_xp = base_xp + event_xp

    history_bonus = min(14.0, math.log1p(max(0.0, total_xp)) * 2.6)
    relationship_strength = affinity_i * 0.6 + tension_i * 0.4 + history_bonus
    level = int(round(_clamp(relationship_strength, 0.0, 100.0) / 100.0 * 15.0))

    recent_7d = int(row.get("recent_7d", 0) or 0)
    previous_21d = int(row.get("previous_21d", 0) or 0)
    trend = _trend(recent_7d, previous_21d, tension_i, last_interaction_at, now)
    archetype = _archetype(
        affinity=affinity_i,
        tension=tension_i,
        reciprocity=reciprocity,
        interaction_count=interaction_count,
        events=shared,
    )

    return RelationshipView(
        chat_id=int(chat_id),
        user_a_id=user_a_id,
        user_b_id=user_b_id,
        user_a_name=str(row.get("user_a_name") or "Участник"),
        user_b_name=str(row.get("user_b_name") or "Участник"),
        affinity=affinity_i,
        tension=tension_i,
        xp=total_xp,
        level=level,
        reciprocity=reciprocity,
        archetype=archetype,
        trend=trend,
        interaction_count=interaction_count,
        reply_count=reply_count,
        mention_count=mention_count,
        reaction_count=reaction_count,
        shared_event_count=len(shared),
        a_to_b_count=int(row.get("a_to_b_count", 0) or 0),
        b_to_a_count=int(row.get("b_to_a_count", 0) or 0),
        recent_7d=recent_7d,
        previous_21d=previous_21d,
        last_interaction_at=last_interaction_at,
        shared_events=shared,
    )


def _synthetic_rows_from_events(events: Iterable[ChronicleEvent], existing: set[tuple[int, int]]) -> list[dict]:
    rows: list[dict] = []
    seen = set(existing)
    for event in events:
        people = list(zip(event.participant_ids, event.participant_names))
        for index, (left_id, left_name) in enumerate(people):
            for right_id, right_name in people[index + 1 :]:
                user_a_id, user_b_id = sorted((int(left_id), int(right_id)))
                key = (user_a_id, user_b_id)
                if key in seen:
                    continue
                seen.add(key)
                names = {int(left_id): left_name, int(right_id): right_name}
                rows.append({
                    "user_a_id": user_a_id,
                    "user_b_id": user_b_id,
                    "user_a_name": names.get(user_a_id) or "Участник",
                    "user_b_name": names.get(user_b_id) or "Участник",
                    "xp": 0.0,
                    "interaction_count": 0,
                    "reply_count": 0,
                    "mention_count": 0,
                    "reaction_count": 0,
                    "a_to_b_count": 0,
                    "b_to_a_count": 0,
                    "a_to_b_weight": 0.0,
                    "b_to_a_weight": 0.0,
                    "recent_7d": 0,
                    "previous_21d": 0,
                    "last_interaction_at": None,
                })
    return rows


async def get_relationships(chat_id: int, *, user_id: int | None = None, persist_snapshots: bool = True) -> list[RelationshipView]:
    rows, events = await asyncio.gather(
        load_relationship_states(chat_id, user_id=user_id),
        list_chronicle_events(chat_id, limit=CHRONICLE_EVENT_LIMIT),
    )
    existing = {(int(row["user_a_id"]), int(row["user_b_id"])) for row in rows}
    synthetic = _synthetic_rows_from_events(events, existing)
    if user_id is not None:
        synthetic = [row for row in synthetic if int(user_id) in {row["user_a_id"], row["user_b_id"]}]
    rows = [*rows, *synthetic]

    now = datetime.now(timezone.utc)
    views = [_calculate_view(chat_id, row, events, now) for row in rows]
    views.sort(key=lambda item: (item.level, item.strength, item.xp), reverse=True)

    if persist_snapshots and views:
        await asyncio.gather(*(save_relationship_snapshot(view) for view in views))
    return views


async def get_relationship(chat_id: int, user_a_id: int, user_b_id: int) -> RelationshipView | None:
    if int(user_a_id) == int(user_b_id):
        return None
    pair = {int(user_a_id), int(user_b_id)}
    views = await get_relationships(chat_id, persist_snapshots=True)
    return next((view for view in views if {view.user_a_id, view.user_b_id} == pair), None)


async def get_relationship_history(chat_id: int, user_a_id: int, user_b_id: int) -> tuple[RelationshipView | None, list[RelationshipSnapshot]]:
    view = await get_relationship(chat_id, user_a_id, user_b_id)
    rows = await load_relationship_snapshots(chat_id, user_a_id, user_b_id, limit=20)
    snapshots = [
        RelationshipSnapshot(
            captured_at=_utc(row.get("captured_at")) or datetime.now(timezone.utc),
            affinity=int(row.get("affinity", 0)),
            tension=int(row.get("tension", 0)),
            xp=float(row.get("xp", 0.0)),
            level=int(row.get("level", 0)),
            reciprocity=float(row.get("reciprocity", 0.0)),
            archetype=str(row.get("archetype") or "периодически пересекаются"),
            trend=str(row.get("trend") or "отношения стабильны"),
        )
        for row in rows
    ]
    return view, snapshots
