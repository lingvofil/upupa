"""Lightweight interaction helpers for diplomatic visits and insults."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from features.world.service import WorldService


INSULT_COOLDOWN = timedelta(minutes=30)


async def record_interaction_event(
    service: WorldService,
    event_type: str,
    *,
    actor_state: int | None = None,
    target_state: int | None = None,
    payload: dict[str, object] | None = None,
    dedupe_key: str | None = None,
) -> bool:
    """Append an interaction event to the shared world ledger."""
    if service.ledger is None:
        return False
    return await asyncio.to_thread(
        service.ledger.record_event,
        event_type,
        actor_state=actor_state,
        target_state=target_state,
        payload=payload,
        dedupe_key=dedupe_key,
    )


async def insult_cooldown_remaining(
    service: WorldService,
    world_id: int,
    *,
    now: datetime | None = None,
) -> timedelta | None:
    """Return remaining state-wide insult cooldown, or None when ready."""
    current = now or datetime.now(timezone.utc)
    events = await service.list_events(
        limit=50,
        days=1,
        world_id=world_id,
        event_types={"state_insult"},
    )
    latest = next((event for event in events if event.actor_state == world_id), None)
    if latest is None:
        return None
    created_at = latest.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    remaining = INSULT_COOLDOWN - (current - created_at)
    return remaining if remaining.total_seconds() > 0 else None
