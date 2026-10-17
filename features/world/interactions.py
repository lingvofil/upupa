"""Diplomatic visits, cooldowns and the World maintenance loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

from features.world.delivery import deliver_text
from features.world.ledger import VISIT_DURATION, VisitRecord
from features.world.service import WorldService, get_world_service
from features.world.visit_feedback import expire_due_feedback_windows, open_feedback_window
from features.world.visit_report import VisitReport, build_visit_report


INSULT_COOLDOWN = timedelta(minutes=30)
INVITE_COOLDOWN = timedelta(hours=1)
VISIT_EXPIRATION_POLL_SECONDS = 60
_visit_delivery_lock = asyncio.Lock()

__all__ = [
    "INSULT_COOLDOWN",
    "INVITE_COOLDOWN",
    "VISIT_DURATION",
    "StateVisit",
    "cooldown_remaining",
    "expire_due_visits",
    "finish_visit",
    "get_open_visit",
    "insult_cooldown_remaining",
    "list_expired_open_visits",
    "notify_visit_finished",
    "record_interaction_event",
    "retry_visit_deliveries",
    "visit_expiration_loop",
    "visit_is_active",
    "world_maintenance_loop",
]


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


def _visit(record: VisitRecord) -> StateVisit:
    return StateVisit(
        host_state=record.host_state,
        guest_state=record.guest_state,
        accepted_event_id=record.accepted_event_id,
        accepted_at=record.accepted_at,
        expires_at=record.expires_at,
    )


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


async def cooldown_remaining(
    service: WorldService,
    event_types: set[str],
    *,
    actor_state: int,
    cooldown: timedelta,
    target_state: int | None = None,
    now: datetime | None = None,
) -> timedelta | None:
    """Time left until ``actor_state`` may repeat an action, or None when ready."""
    latest = await service.latest_event(event_types, actor_state=actor_state, target_state=target_state)
    if latest is None:
        return None
    current = _utc(now or datetime.now(timezone.utc))
    remaining = cooldown - (current - _utc(latest.created_at))
    return remaining if remaining.total_seconds() > 0 else None


async def insult_cooldown_remaining(
    service: WorldService,
    world_id: int,
    *,
    now: datetime | None = None,
) -> timedelta | None:
    """Return remaining state-wide insult cooldown, or None when ready."""
    return await cooldown_remaining(
        service,
        {"state_insult"},
        actor_state=world_id,
        cooldown=INSULT_COOLDOWN,
        now=now,
    )


async def get_open_visit(
    service: WorldService,
    host_state: int,
    guest_state: int,
) -> StateVisit | None:
    """Return the latest accepted visit between this host/guest pair if not finished yet."""
    if service.ledger is None:
        return None
    record = await asyncio.to_thread(service.ledger.get_open_visit, host_state, guest_state)
    return _visit(record) if record is not None else None


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
            "accepted_at": visit.accepted_at.isoformat(),
            "expires_at": visit.expires_at.isoformat(),
            "delivery_version": 1,
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
    if service.ledger is None:
        return ()
    current = _utc(now or datetime.now(timezone.utc))
    records = await asyncio.to_thread(service.ledger.list_expired_open_visits, current)
    return tuple(_visit(record) for record in records)


async def _delivery_event(service: WorldService, key: str):
    if service.ledger is None:
        return None
    return await asyncio.to_thread(service.ledger.get_event_by_dedupe_key, key)


async def notify_visit_finished(bot, service: WorldService, visit: StateVisit, *, reason: str) -> None:
    # Manual completion and the periodic retry may see the same visit.
    async with _visit_delivery_lock:
        await _deliver_visit_finished(bot, service, visit, reason=reason)


async def _deliver_visit_finished(bot, service: WorldService, visit: StateVisit, *, reason: str) -> None:
    if await _delivery_event(service, f"visit_delivery_done:{visit.accepted_event_id}"):
        return
    host, guest = await asyncio.gather(
        service.get_state_by_world_id(visit.host_state),
        service.get_state_by_world_id(visit.guest_state),
    )
    if host is None or guest is None:
        return

    cached = await _delivery_event(service, f"visit_report:{visit.accepted_event_id}")
    if cached is not None:
        report = VisitReport(
            text=str(cached.payload["summary"]),
            showcase_count=int(cached.payload["showcase_count"]),
            contributor_count=int(cached.payload["contributor_count"]),
        )
        report_display = report.text
    else:
        finished = await _delivery_event(service, f"visit_finished:{visit.accepted_event_id}")
        report = await build_visit_report(
            service,
            host_state=visit.host_state,
            guest_state=visit.guest_state,
            accepted_at=visit.accepted_at,
            host_chat_id=host.chat_id,
            host_title=host.title,
            guest_title=guest.title,
            finished_at=finished.created_at if finished else None,
        )
        report_display = report.text.replace("Экскурс", "Екскурс").replace("экскурс", "екскурс")
        await record_interaction_event(
            service,
            "state_visit_report",
            actor_state=visit.host_state,
            target_state=visit.guest_state,
            payload={
                "accepted_event_id": visit.accepted_event_id,
                "showcase_count": report.showcase_count,
                "contributor_count": report.contributor_count,
                "summary": report_display,
            },
            dedupe_key=f"visit_report:{visit.accepted_event_id}",
        )

    auto = " Прошло 24 часа." if reason == "timeout" else ""
    report_text = (
        "\n\n📋 Что удалось показать:\n"
        f"{report_display}\n\n"
        f"Показано: {report.showcase_count} · екскурсоводов: {report.contributor_count}"
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
    delivered = True
    for state, text, label in ((host, host_text, "host"), (guest, guest_text, "guest")):
        delivery_key = f"visit_delivery:{visit.accepted_event_id}:{label}"
        if await _delivery_event(service, delivery_key):
            continue
        sent = await deliver_text(bot, service, state, text)
        if sent is None:
            delivered = False
            continue
        await record_interaction_event(
            service,
            "state_visit_notification_delivered",
            payload={"accepted_event_id": visit.accepted_event_id, "side": label},
            dedupe_key=delivery_key,
        )

    window = await open_feedback_window(
        bot,
        service,
        accepted_event_id=visit.accepted_event_id,
        host_state=host.world_id,
        guest_state=guest.world_id,
        host_title=host.title,
        guest_chat_id=guest.chat_id,
    )

    # A side that left the World can never receive the report; don't retry forever.
    host_now, guest_now = await asyncio.gather(
        service.get_state_by_world_id(visit.host_state),
        service.get_state_by_world_id(visit.guest_state),
    )
    gone = (host_now is None or not host_now.enabled) or (guest_now is None or not guest_now.enabled)
    if (delivered and window is not None) or gone:
        await record_interaction_event(
            service,
            "state_visit_delivery_completed",
            payload={"accepted_event_id": visit.accepted_event_id},
            dedupe_key=f"visit_delivery_done:{visit.accepted_event_id}",
        )


async def retry_visit_deliveries(bot, service: WorldService) -> None:
    if service.ledger is None:
        return
    pending = await asyncio.to_thread(service.ledger.list_undelivered_finished_visits)
    for record in pending:
        try:
            await notify_visit_finished(
                bot,
                service,
                _visit(record),
                reason=record.finish_reason or "manual",
            )
        except Exception:
            logging.exception("World visit delivery retry failed visit=%s", record.accepted_event_id)


async def expire_due_visits(bot, service: WorldService | None = None) -> int:
    """Close and notify all visits whose 24-hour window has elapsed."""
    service = service or get_world_service()
    await retry_visit_deliveries(bot, service)
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
        try:
            await notify_visit_finished(bot, service, visit, reason="timeout")
        except Exception:
            logging.exception("World visit delivery failed visit=%s", visit.accepted_event_id)
    return closed


async def run_world_maintenance(bot, service: WorldService | None = None) -> None:
    """One pass over every timed World process; each step is isolated."""
    from features.world.battles import finish_due_battles
    from features.world.blocs import expire_bloc_applications
    from features.world.expansion import decide_due_cases, expire_due_sanctions
    from features.world.newspaper import issue_weekly_newspaper_if_due

    service = service or get_world_service()
    steps = (
        ("visits", expire_due_visits),
        ("feedback", expire_due_feedback_windows),
        ("battles", finish_due_battles),
        ("court", decide_due_cases),
        ("sanctions", expire_due_sanctions),
        ("blocs", expire_bloc_applications),
        ("newspaper", issue_weekly_newspaper_if_due),
    )
    for name, step in steps:
        try:
            await step(bot, service)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("World maintenance step failed: %s", name)


async def world_maintenance_loop(bot) -> None:
    """Periodically close visits, feedback windows, battles, court cases and more."""
    try:
        from features.world.expansion import reset_stuck_deliberations

        await reset_stuck_deliberations()
    except Exception:
        logging.exception("World court recovery failed")
    while True:
        try:
            await run_world_maintenance(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("World maintenance loop failed")
        await asyncio.sleep(VISIT_EXPIRATION_POLL_SECONDS)


# Historical name wired in the composition root.
visit_expiration_loop = world_maintenance_loop
