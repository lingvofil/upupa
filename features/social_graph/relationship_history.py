"""Integrated relationship history built from existing durable data sources.

The service deliberately does not read or re-analyse raw chat messages.  It
assembles a pair timeline from relationship snapshots, Chronicle events and
small read-only adapters registered by modules that already persist their own
social/game events.  The adapters expose existing records; this module does not
copy them into a third store.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import re

from features.chronicle.models import ChronicleEvent
from features.social_graph.relationships import (
    RelationshipSnapshot,
    RelationshipView,
    get_relationship_history,
)


MAX_TIMELINE_ITEMS = 10
_SNAPSHOT_CHRONICLE_WINDOW = timedelta(hours=36)
_EVENT_MATCH_WINDOW = timedelta(hours=24)
_WORD_RE = re.compile(r"[\wа-яё]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SharedRelationshipEvent:
    """Normalized view over an event already persisted by another module."""

    timestamp: datetime
    event_type: str
    title: str
    summary: str
    source: str
    priority: int = 60


SharedEventProvider = Callable[[int, int, int], Iterable[SharedRelationshipEvent]]
_shared_event_providers: dict[str, SharedEventProvider] = {}


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


def register_shared_relationship_event_provider(name: str, provider: SharedEventProvider) -> None:
    """Register an idempotent read-only adapter for another module's persisted events."""
    key = str(name or "").strip().casefold()
    if not key:
        raise ValueError("shared relationship event provider name is required")
    _shared_event_providers[key] = provider


def list_shared_relationship_events(chat_id: int, user_a_id: int, user_b_id: int) -> tuple[SharedRelationshipEvent, ...]:
    events: list[SharedRelationshipEvent] = []
    for name, provider in tuple(_shared_event_providers.items()):
        try:
            events.extend(provider(int(chat_id), int(user_a_id), int(user_b_id)) or ())
        except Exception as exc:
            logging.warning("Relationship shared-event provider failed provider=%s: %s", name, exc)
    return tuple(events)


def _short_text(value: str, limit: int = 320) -> str:
    compact = " ".join(str(value or "").split()).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


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
        summary = _short_text(event.summary) or "Событие попало в Летопись без отдельного описания."
        source = f"chronicle:{event.source or 'unknown'}"
        items.append(
            RelationshipTimelineItem(
                timestamp=event.event_started_at,
                event_type=f"chronicle_{event.category}",
                title=_short_text(event.title, 160) or "Событие из Летописи",
                summary=summary,
                source=source,
                priority=100 + int(max(0.0, min(99.0, event.importance_score))),
                chronicle_event=event,
            )
        )
    return items


def _shared_items(events: Iterable[SharedRelationshipEvent]) -> list[RelationshipTimelineItem]:
    return [
        RelationshipTimelineItem(
            timestamp=event.timestamp,
            event_type=event.event_type,
            title=_short_text(event.title, 160) or "Совместное событие",
            summary=_short_text(event.summary) or "Сохранённое совместное событие.",
            source=event.source,
            priority=max(0, min(99, int(event.priority))),
        )
        for event in events
    ]


def _normalized_tokens(value: str) -> set[str]:
    return {token.casefold() for token in _WORD_RE.findall(value) if len(token) >= 3}


def _same_event(left: RelationshipTimelineItem, right: RelationshipTimelineItem) -> bool:
    left_event = left.chronicle_event
    right_event = right.chronicle_event
    if left_event is not None and right_event is not None:
        if left_event.id == right_event.id:
            return True
        left_messages = set(left_event.source_message_ids)
        right_messages = set(right_event.source_message_ids)
        if left_messages and right_messages and left_messages.intersection(right_messages):
            return True

    if abs(left.timestamp - right.timestamp) > _EVENT_MATCH_WINDOW:
        return False
    left_tokens = _normalized_tokens(f"{left.title} {left.summary}")
    right_tokens = _normalized_tokens(f"{right.title} {right.summary}")
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens.intersection(right_tokens)) / max(1, min(len(left_tokens), len(right_tokens)))
    return overlap >= 0.65


def _deduplicate(items: Iterable[RelationshipTimelineItem]) -> list[RelationshipTimelineItem]:
    chronicle: list[RelationshipTimelineItem] = []
    shared: list[RelationshipTimelineItem] = []
    snapshots: list[RelationshipTimelineItem] = []

    for item in sorted(items, key=lambda value: (value.timestamp, -value.priority)):
        if item.source == "relationship_snapshot":
            snapshots.append(item)
            continue
        target = chronicle if item.chronicle_event is not None else shared
        duplicate_index = next(
            (index for index, existing in enumerate(target) if _same_event(existing, item)),
            None,
        )
        if duplicate_index is None:
            target.append(item)
        elif item.priority > target[duplicate_index].priority:
            target[duplicate_index] = item

    # Chronicle is the richest normalized record.  If a module-specific event
    # describes the same real episode, keep Chronicle only.
    shared = [item for item in shared if not any(_same_event(item, event) for event in chronicle)]

    # A relationship snapshot commonly reflects a Chronicle event that happened
    # just before somebody asked for the relationship card.  The Chronicle row is
    # more useful to the user, so avoid a second generic timeline item nearby.
    snapshots = [
        snapshot
        for snapshot in snapshots
        if not any(abs(snapshot.timestamp - event.timestamp) <= _SNAPSHOT_CHRONICLE_WINDOW for event in chronicle)
    ]

    result = [*chronicle, *shared, *snapshots]
    result.sort(key=lambda item: item.timestamp)
    return result[-MAX_TIMELINE_ITEMS:]


def assemble_relationship_history(
    view: RelationshipView,
    snapshots: Iterable[RelationshipSnapshot],
    shared_events: Iterable[SharedRelationshipEvent] = (),
) -> RelationshipHistory:
    snapshot_tuple = tuple(snapshots)
    timeline = _deduplicate([
        *_snapshot_items(snapshot_tuple),
        *_chronicle_items(view.shared_events),
        *_shared_items(shared_events),
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
    shared_events = list_shared_relationship_events(chat_id, user_a_id, user_b_id)
    return assemble_relationship_history(view, snapshots, shared_events)
