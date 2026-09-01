"""Lightweight interaction helpers for diplomatic visits and insults."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

from features.world.service import WorldService, get_world_service
from features.world.visit_report import build_visit_report


INSULT_COOLDOWN = timedelta(minutes=30)
VISIT_DURATION = timedelta(hours=24)
VISIT_EXPIRATION_POLL_SECONDS = 300
_VISIT_EVENT_TYPES = {"state_visit_accepted", "state_visit_finished"}


@dataclass(frozen=True)
class StateVisit:
    host_state: int
    guest_state: int
    accepted_event_id: int
    accepted_at: datetime
    expires_at: datetime


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def visit_is_active(visit: StateVisit, *, now: datetime | None = None) -> bool:
    current = _utc(now or datetime.now(timezone.utc))
    return current < visit.expires_at


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


async def get_open_visit(
    service: WorldService,
    host_state: int,
    guest_state: int,
) -> StateVisit | None:
    """Return the latest accepted visit between this host/guest pair if not finished yet."""
    events = await service.list_events(
        limit=200,
        days=7,
        world_id=host_state,
        event_types=_VISIT_EVENT_TYPES,
    )
    for event in events:
        if (
            event.event_type == "state_visit_finished"
            and event.actor_state == host_state
            and event.target_state == guest_state
        ):
            return None
        if (
            event.event_type == "state_visit_accepted"
            and event.actor_state == guest_state
            and event.target_state == host_state
        ):
            accepted_at = _utc(event.created_at)
            return StateVisit(
                host_state=host_state,
                guest_state=guest_state,
                accepted_event_id=event.event_id,
                accepted_at=accepted_at,
                expires_at=accepted_at + VISIT_DURATION,
            )
    return None


async def finish_visit(
    service: WorldService,
    host_state: int,
    guest_state: int,
    *,
    reason: str,
    finished_by: str | None = None,
) -> StateVisit | None:
    """Close the latest open visit once and return the visit that was closed."""
    visit = await get_open_visit(service, host_state, guest_state)
    if visit is None:
        return None
    recorded = await record_interaction_event(
        service,
        "state_visit_finished",
        actor_state=host_state,
        target_state=guest_state,
        payload={
            "reason": reason,
            "finished_by": finished_by or "",
            "accepted_event_id": visit.accepted_event_id,
        },
        dedupe_key=f"visit_finished:{visit.accepted_event_id}",
    )
    return visit if recorded else None


async def list_expired_open_visits(
    service: WorldService,
    *,
    now: datetime | None = None,
) -> tuple[StateVisit, ...]:
    """Return visits whose 24-hour window elapsed and which have no finish event."""
    current = _utc(now or datetime.now(timezone.utc))
    events = await service.list_events(
        limit=500,
        days=7,
        event_types=_VISIT_EVENT_TYPES,
    )
    latest_by_pair = {}
    for event in events:
        if event.actor_state is None or event.target_state is None:
            continue
        if event.event_type == "state_visit_accepted":
            key = (event.target_state, event.actor_state)
        else:
            key = (event.actor_state, event.target_state)
        latest_by_pair.setdefault(key, event)

    expired = []
    for (host_state, guest_state), event in latest_by_pair.items():
        if event.event_type != "state_visit_accepted":
            continue
        accepted_at = _utc(event.created_at)
        visit = StateVisit(
            host_state=host_state,
            guest_state=guest_state,
            accepted_event_id=event.event_id,
            accepted_at=accepted_at,
            expires_at=accepted_at + VISIT_DURATION,
        )
        if current >= visit.expires_at:
            expired.append(visit)
    return tuple(expired)


async def notify_visit_finished(bot, service: WorldService, visit: StateVisit, *, reason: str) -> None:
    host, guest = await asyncio.gather(
        service.get_state_by_world_id(visit.host_state),
        service.get_state_by_world_id(visit.guest_state),
    )
    if host is None or guest is None:
        return

    report = await build_visit_report(
        service,
        host_state=visit.host_state,
        guest_state=visit.guest_state,
        accepted_at=visit.accepted_at,
        host_chat_id=host.chat_id,
        host_title=host.title,
        guest_title=guest.title,
    )
    await record_interaction_event(
        service,
        "state_visit_report",
        actor_state=visit.host_state,
        target_state=visit.guest_state,
        payload={
            "accepted_event_id": visit.accepted_event_id,
            "showcase_count": report.showcase_count,
            "contributor_count": report.contributor_count,
            "summary": report.text,
        },
        dedupe_key=f"visit_report:{visit.accepted_event_id}",
    )

    auto = " Прошло 24 часа." if reason == "timeout" else ""
    report_text = (
        "\n\n📋 Что удалось показать:\n"
        f"{report.text}\n\n"
        f"Показано: {report.showcase_count} · экскурсоводов: {report.contributor_count}"
    )
    host_text = (
        f"🛫 Государственный визит завершён.{auto}\n\n"
        f"Делегация государства №{guest.world_id} — {guest.title} отбыла. "
        "Унесли с собой противоречивые впечатления и один пакет."
        f"{report_text}"
    )
    guest_text = (
        f"🛫 Визит в государство №{host.world_id} — {host.title} завершён.{auto}\n\n"
        "Делегация вернулась домой. Впечатления противоречивые, пакет при них."
        f"{report_text}"
    )
    for chat_id, text, label in (
        (host.chat_id, host_text, "host"),
        (guest.chat_id, guest_text, "guest"),
    ):
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logging.exception(
                "World visit finish notification failed side=%s host=%s guest=%s",
                label,
                visit.host_state,
                visit.guest_state,
            )


async def expire_due_visits(bot, service: WorldService | None = None) -> int:
    """Close and notify all visits whose 24-hour window has elapsed."""
    service = service or get_world_service()
    expired = await list_expired_open_visits(service)
    closed = 0
    for candidate in expired:
        visit = await finish_visit(
            service,
            candidate.host_state,
            candidate.guest_state,
            reason="timeout",
        )
        if visit is None:
            continue
        closed += 1
        await notify_visit_finished(bot, service, visit, reason="timeout")
    return closed


async def visit_expiration_loop(bot) -> None:
    """Periodically close state visits after 24 hours."""
    while True:
        try:
            await expire_due_visits(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("World visit expiration loop failed")
        await asyncio.sleep(VISIT_EXPIRATION_POLL_SECONDS)
