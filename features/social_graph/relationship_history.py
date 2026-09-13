"""Integrated relationship history built from durable relationship and Chronicle data.

This module deliberately does not read the raw chat history.  It assembles a
pair timeline from the relationship snapshots that already exist in the social
graph and Chronicle events that already include both participants.  External
game/social events are therefore included automatically when they were saved
through Chronicle's universal external-candidate entry point.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Iterable

from features.chronicle.models import ChronicleEvent
from features.social_graph.relationships import (
    RelationshipSnapshot,
    RelationshipView,
    get_relationship_history,
)


MAX_TIMELINE_ITEMS = 12
_SNAPSHOT_CHRONICLE_WINDOW = timedelta(hours=36)
_WORD_RE = re.compile(r"[\wа-яё]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RelationshipTimelineItem:
    timestamp: datetime
    event_type: str
    title: str
    summary: str
    source: str
    priority: int
    chronicle_event: ChronicleEvent | None = None


@dataclass(frozen=True, slots=True)
class RelationshipHistory:
    view: RelationshipView
    snapshots: tuple[RelationshipSnapshot, ...]
    timeline: tuple[RelationshipTimelineItem, ...]


def _snapshot_summary(previous: RelationshipSnapshot, current: RelationshipSnapshot) -> tuple[str, str] | None:
    changes: list[str] = []
    event_type = "relationship_shift"

    if previous.archetype != current.archetype:
        changes.append(f"тип связи: «{previous.archetype}» → «{current.archetype}»")
        event_type = "archetype_change"
    if previous.level != current.level:
        changes.append(f"уровень: {previous.level}/15 → {current.level}/15")
        if event_type == "relationship_shift":
            event_type = "level_change"

    affinity_delta = current.affinity - previous.affinity
    if abs(affinity_delta) >= 15:
        direction = "выросла" if affinity_delta > 0 else "снизилась"
        changes.append(f"близость заметно {direction}")

    tension_delta = current.tension - previous.tension
    if abs(tension_delta) >= 15:
        direction = "выросло" if tension_delta > 0 else "снизилось"
        changes.append(f"напряжение заметно {direction}")

    reciprocity_delta = current.reciprocity - previous.reciprocity
    if abs(reciprocity_delta) >= 0.20:
        direction = "выросла" if reciprocity_delta > 0 else "снизилась"
        changes.append(f"взаимность заметно {direction}")

    if previous.trend != current.trend and current.trend != "отношения стабильны":
        changes.append(f"тренд: {current.trend}")

    if not changes:
        return None
    return event_type, "; ".join(changes) + "."


def _snapshot_items(snapshots: Iterable[RelationshipSnapshot]) -> list[RelationshipTimelineItem]:
    ordered = sorted(snapshots, key=lambda item: item.captured_at)
    if not ordered:
        return []

    items = [
        RelationshipTimelineItem(
            timestamp=ordered[0].captured_at,
            event_type="relationship_snapshot",
            title="Первый сохранённый снимок отношений",
            summary=f"Уровень {ordered[0].level}/15 — «{ordered[0].archetype}»; {ordered[0].trend}.",
            source="relationship_snapshot",
            priority=10,
        )
    ]
    for previous, current in zip(ordered, ordered[1:]):
        change = _snapshot_summary(previous, current)
        if change is None:
            continue
        event_type, summary = change
        if event_type == "archetype_change":
            title = "Смена типа связи"
        elif event_type == "level_change":
            title = "Новый уровень отношений"
        else:
            title = "Заметный сдвиг в отношениях"
        items.append(
            RelationshipTimelineItem(
                timestamp=current.captured_at,
                event_type=event_type,
                title=title,
                summary=summary,
                source="relationship_snapshot",
                priority=20,
            )
        )
    return items


def _chronicle_items(events: Iterable[ChronicleEvent]) -> list[RelationshipTimelineItem]:
    items = []
    for event in events:
        summary = event.summary.strip() or "Событие попало в Летопись без отдельного описания."
        source = f"chronicle:{event.source or 'unknown'}"
        items.append(
            RelationshipTimelineItem(
                timestamp=event.event_started_at,
                event_type=f"chronicle_{event.category}",
                title=event.title.strip() or "Событие из Летописи",
                summary=summary,
                source=source,
                priority=100 + int(max(0.0, min(99.0, event.importance_score))),
                chronicle_event=event,
            )
        )
    return items


def _normalized_tokens(value: str) -> set[str]:
    return {token.casefold() for token in _WORD_RE.findall(value) if len(token) >= 3}


def _same_chronicle_event(left: RelationshipTimelineItem, right: RelationshipTimelineItem) -> bool:
    if left.chronicle_event is None or right.chronicle_event is None:
        return False
    if left.chronicle_event.id == right.chronicle_event.id:
        return True
    left_messages = set(left.chronicle_event.source_message_ids)
    right_messages = set(right.chronicle_event.source_message_ids)
    if left_messages and right_messages and left_messages.intersection(right_messages):
        return True
    if abs(left.timestamp - right.timestamp) > timedelta(hours=18):
        return False
    left_tokens = _normalized_tokens(f"{left.title} {left.summary}")
    right_tokens = _normalized_tokens(f"{right.title} {right.summary}")
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens.intersection(right_tokens)) / max(1, min(len(left_tokens), len(right_tokens)))
    return overlap >= 0.65


def _deduplicate(items: Iterable[RelationshipTimelineItem]) -> list[RelationshipTimelineItem]:
    chronicle: list[RelationshipTimelineItem] = []
    snapshots: list[RelationshipTimelineItem] = []
    for item in sorted(items, key=lambda value: (value.timestamp, -value.priority)):
        if item.chronicle_event is None:
            snapshots.append(item)
            continue
        duplicate_index = next(
            (index for index, existing in enumerate(chronicle) if _same_chronicle_event(existing, item)),
            None,
        )
        if duplicate_index is None:
            chronicle.append(item)
        elif item.priority > chronicle[duplicate_index].priority:
            chronicle[duplicate_index] = item

    # A relationship snapshot commonly reflects a Chronicle event that happened
    # just before somebody asked for the relationship card.  In that case the
    # Chronicle entry is the richer record and wins instead of showing the same
    # real-world episode twice.
    filtered_snapshots = [
        snapshot
        for snapshot in snapshots
        if not any(abs(snapshot.timestamp - event.timestamp) <= _SNAPSHOT_CHRONICLE_WINDOW for event in chronicle)
    ]
    result = [*chronicle, *filtered_snapshots]
    result.sort(key=lambda item: item.timestamp)
    return result[-MAX_TIMELINE_ITEMS:]


def assemble_relationship_history(
    view: RelationshipView,
    snapshots: Iterable[RelationshipSnapshot],
) -> RelationshipHistory:
    snapshot_tuple = tuple(snapshots)
    timeline = _deduplicate([
        *_snapshot_items(snapshot_tuple),
        *_chronicle_items(view.shared_events),
    ])
    return RelationshipHistory(view=view, snapshots=snapshot_tuple, timeline=tuple(timeline))


async def get_integrated_relationship_history(
    chat_id: int,
    user_a_id: int,
    user_b_id: int,
) -> RelationshipHistory | None:
    """Load current pair state and compose its history without raw-message analysis."""
    view, snapshots = await get_relationship_history(chat_id, user_a_id, user_b_id)
    if view is None:
        return None
    return assemble_relationship_history(view, snapshots)
