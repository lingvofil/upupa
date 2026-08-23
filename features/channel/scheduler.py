"""Пять автономных постов Упупы в случайное время каждого дня."""

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
POSTS_PER_DAY = 5
PREFERRED_GAP_MINUTES = 120
MIN_GAP_MINUTES = 45
STARTUP_LEAD_MINUTES = 5
MISSED_GRACE_MINUTES = 20
RETRY_MINUTES = 30
CHECK_INTERVAL_SECONDS = 30


def _local_dt(day: date, clock: time) -> datetime:
    return MOSCOW_TZ.localize(datetime.combine(day, clock))


def _pick_daily_slots(day: date, *, now: datetime | None = None, rng=random) -> list[datetime]:
    """Выбирает до пяти будущих слотов, стараясь не ставить публикации кучно."""
    start = _local_dt(day, DAY_START)
    end = _local_dt(day, DAY_END)
    if now is not None and now.date() == day:
        start = max(start, now.astimezone(MOSCOW_TZ) + timedelta(minutes=STARTUP_LEAD_MINUTES))

    span_minutes = int((end - start).total_seconds() // 60)
    if span_minutes < 0:
        return []
    if span_minutes == 0:
        return [start]

    # При обычном старте хватает места на все 5 постов с интервалом >= 2 часов.
    # При позднем рестарте уменьшаем количество слотов, а не устраиваем очередь из постов.
    count = min(POSTS_PER_DAY, span_minutes // MIN_GAP_MINUTES + 1)
    if count <= 1:
        return [start + timedelta(minutes=rng.randint(0, span_minutes))]

    effective_gap = min(PREFERRED_GAP_MINUTES, span_minutes // (count - 1))
    slack = span_minutes - effective_gap * (count - 1)

    # Если вычесть обязательные промежутки, остаётся slack. Случайно распределяем
    # его между позициями; после возврата gap гарантирован математически.
    jitter = sorted(rng.randint(0, slack) for _ in range(count)) if slack > 0 else [0] * count
    offsets = [jitter[index] + index * effective_gap for index in range(count)]
    return [start + timedelta(minutes=offset) for offset in offsets]


def _new_schedule(now: datetime) -> dict:
    slots = _pick_daily_slots(now.date(), now=now)
    return {
        "date": now.date().isoformat(),
        "target_posts": POSTS_PER_DAY,
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
    """После долгого рестарта переносит просроченные слоты вперёд вместо burst-публикации."""
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


def _top_up_schedule_slots(state: dict, now: datetime, rng=random) -> bool:
    """Миграция старого дневного расписания: добавляет будущие слоты до нового лимита 5."""
    slots = state.setdefault("slots", [])
    changed = False

    if state.get("target_posts") != POSTS_PER_DAY:
        state["target_posts"] = POSTS_PER_DAY
        changed = True

    if len(slots) >= POSTS_PER_DAY:
        return changed

    occupied = [parsed for slot in slots if (parsed := _parse_slot(slot.get("at"))) is not None]
    candidates = _pick_daily_slots(now.date(), now=now, rng=rng)

    for candidate in candidates:
        if len(slots) >= POSTS_PER_DAY:
            break
        if any(abs((candidate - other).total_seconds()) < MIN_GAP_MINUTES * 60 for other in occupied):
            continue
        slots.append({"at": candidate.isoformat(), "done": False, "missed": False})
        occupied.append(candidate)
        changed = True

    slots.sort(key=lambda slot: str(slot.get("at") or ""))
    return changed


def _get_schedule_for_now(now: datetime) -> dict:
    state = load_schedule()
    if state.get("date") != now.date().isoformat():
        state = _new_schedule(now)
        save_schedule(state)
        logging.info("[channel] daily slots: %s", [slot["at"] for slot in state["slots"]])
        return state

    changed = _repair_missed_slots(state, now)
    changed = _top_up_schedule_slots(state, now) or changed
    if changed:
        save_schedule(state)
        logging.info("[channel] adjusted daily slots: %s", [slot["at"] for slot in state["slots"]])
    return state


async def channel_scheduler_loop(bot) -> None:
    """Фоновый цикл: публикует до пяти случайно запланированных постов в сутки."""
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
