"""Data structures shared by Chronicle detection, persistence and UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ChronicleCandidate:
    id: int
    chat_id: int
    candidate_key: str
    anchor_message_id: int | None
    started_at: datetime
    last_activity_at: datetime
    due_at: datetime
    anchor_text: str
    anchor_user_id: int | None
    anchor_display_name: str
    anchor_username: str | None
    score: float
    reaction_count: int
    unique_reactors: int
    reply_count: int
    participant_count: int
    participant_ids: list[int] = field(default_factory=list)
    source_message_ids: list[int] = field(default_factory=list)
    source: str = "live"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChronicleEventDraft:
    title: str
    summary: str
    category: str
    confidence: float
    participant_ids: list[int]
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    ai_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChronicleEvent:
    id: str
    chat_id: int
    created_at: datetime
    event_started_at: datetime
    event_ended_at: datetime
    title: str
    summary: str
    category: str
    importance_score: float
    participant_ids: list[int]
    participant_names: list[str]
    participant_usernames: list[str | None]
    source_message_ids: list[int]
    anchor_message_id: int | None
    reaction_count: int
    unique_reactors: int
    reply_count: int
    keywords: list[str]
    entities: list[str]
    related_event_ids: list[str]
    ai_metadata: dict[str, Any]
    source: str
