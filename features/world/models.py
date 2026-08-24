"""Domain models for the opt-in inter-chat World of Upupa."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class WorldState:
    world_id: int
    chat_id: int
    title: str
    created_at: datetime
    enabled: bool


@dataclass(frozen=True)
class DiplomaticRequest:
    request_id: int
    source_state: int
    target_state: int
    status: str
    created_at: datetime
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class WorldProfile:
    state: WorldState
    allies: tuple[WorldState, ...]
    neutral: tuple[WorldState, ...]
    inactive_allies: tuple[WorldState, ...]


@dataclass(frozen=True)
class ProposalResult:
    status: str
    source: WorldState | None = None
    target: WorldState | None = None
    request: DiplomaticRequest | None = None


@dataclass(frozen=True)
class ResolutionResult:
    status: str
    request: DiplomaticRequest | None = None
    source: WorldState | None = None
    target: WorldState | None = None


@dataclass(frozen=True)
class BreakAllianceResult:
    status: str
    source: WorldState | None = None
    target: WorldState | None = None
