"""Два автономных поста Упупы в случайное время каждого дня."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime, time, timedelta

import pytz

from features.channel.service import publish_channel_post
from features.channel.storage import load_schedule, save_schedule

MOSCOW_TZ = pytz.timezone("Europe/Moscow")
DAY_START = time(10, 0)
DAY_END = time(23, 30)
POSTS_PER_DAY = 2
PREFERRED_GAP_MINUTES = 180
STARTUP_LEAD_MINUTES = 5
MISSED_GRACE_MINUTES = 20
RETRY_MINUTES = 30
CHECK_INTERVAL_SECONDS = 30


def _local_dt(day: date, clock: time) -> datetime:
    return MOSCOW_TZ.localize(datetime.combine(day, clock))


def _pick_daily_slots(day: date, *, now: datetime | None = None, rng=random) -> list[datetime]:
    """Выбирает до двух будущих слотов; в обычный день между ними стараемся держать 3 часа."""
    start = _local_dt(day, DAY_START)
    end = _local_dt(day, DAY_END)
    if now is not None and now.date() == day:
        start = max(start, now.astimezone(MOSCOW_TZ) + timedelta(minutes=STARTUP_LEAD_MINUTES))

    span_minutes = int((end - start).total_seconds() // 60)
    if span_minutes < 0:
        return []
    if span_minutes == 0:
        return [start]

    effective_gap = min(PREFERRED_GAP_MINUTES, max(30, span_minutes // 2))
    for _ in range(500):
        first = rng.randint(0, span_minutes)
        second = rng.randint(0, span_minutes)
        if first == second:
            continue
        if abs(first - second) >= effective_gap:
            return sorted([start + timedelta(minutes=first), start + timedelta(minutes=second)])

    offsets = sorted(rng.sample(range(span_minutes + 1), k=2))
    return [start + timedelta(minutes=offset) for offset in offsets]


def _new_schedule(now: datetime) -> dict:
    slots = _pick_daily_slots(now.date(), now=now)
    return {
        "date": now.date().isoformat(),
        "slots": [
            {"at": slot.isoformat(), "done": False, "missed": False}
            for slot in slots
        ],
    }


def _parse_slot(raw: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        return MOSCOW_TZ.localize(value)
    return value.astimezone(MOSCOW_TZ)


def _random_future_slot(now: datetime, rng=random) -> datetime | None:
    earliest = now + timedelta(minutes=STARTUP_LEAD_MINUTES)
    latest = _local_dt(now.date(), DAY_END)
    span = int((latest - earliest).total_seconds() // 60)
    if span < 0:
        return None
    return earliest + timedelta(minutes=rng.randint(0, span))


def _repair_missed_slots(state: dict, now: datetime, rng=random) -> bool:
    """После долгого рестарта не вываливает два просроченных поста подряд, а переносит их вперёд."""
    changed = False
    for slot in state.get("slots", []):
        if slot.get("done"):
            continue
        scheduled = _parse_slot(slot.get("at"))
        if scheduled is None:
            replacement = _random_future_slot(now, rng=rng)
            if replacement is None:
                slot.update(done=True, missed=True)
            else:
                slot["at"] = replacement.isoformat()
            changed = True
            continue
        if now - scheduled <= timedelta(minutes=MISSED_GRACE_MINUTES):
            continue
        replacement = _random_future_slot(now, rng=rng)
        if replacement is None:
            slot.update(done=True, missed=True)
        else:
            slot["at"] = replacement.isoformat()
        changed = True
    return changed


def _get_schedule_for_now(now: datetime) -> dict:
    state = load_schedule()
    if state.get("date") != now.date().isoformat():
        state = _new_schedule(now)
        save_schedule(state)
        logging.info("[channel] daily slots: %s", [slot["at"] for slot in state["slots"]])
        return state

    if _repair_missed_slots(state, now):
        save_schedule(state)
    return state


async def channel_scheduler_loop(bot) -> None:
    """Фоновый цикл: публикует два случайно запланированных поста в сутки."""
    await asyncio.sleep(60)
    while True:
        try:
            now = datetime.now(MOSCOW_TZ)
            state = await asyncio.to_thread(_get_schedule_for_now, now)
            changed = False

            for slot in state.get("slots", []):
                if slot.get("done"):
                    continue
                scheduled = _parse_slot(slot.get("at"))
                if scheduled is None or now < scheduled:
                    continue

                try:
                    await publish_channel_post(bot, source="scheduled")
                except Exception as exc:
                    logging.error("[channel] scheduled post failed: %s", exc, exc_info=True)
                    retry_at = now + timedelta(minutes=RETRY_MINUTES)
                    if retry_at <= _local_dt(now.date(), DAY_END):
                        slot["at"] = retry_at.isoformat()
                    else:
                        slot.update(done=True, missed=True)
                else:
                    slot["done"] = True
                changed = True

            if changed:
                await asyncio.to_thread(save_schedule, state)
        except Exception as exc:
            logging.error("[channel] scheduler loop error: %s", exc, exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
