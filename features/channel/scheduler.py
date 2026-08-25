"""Autonomous channel scheduler with mood-dependent daily activity."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import date, datetime, time, timedelta

import pytz

from features.channel.mood import burst_probability, daily_post_target, get_current_mood
from features.channel.mood_service import publish_channel_post
from features.channel.storage import load_schedule, save_schedule

MOSCOW_TZ = pytz.timezone("Europe/Moscow")
DAY_START = time(0, 0)
DAY_END = time(23, 59, 59)

# Deprecated compatibility target for old direct helper callers. Production uses NOMINAL_POSTS_PER_DAY and mood ranges.
POSTS_PER_DAY = 5
NOMINAL_POSTS_PER_DAY = 10
PREFERRED_GAP_MINUTES = 120
MIN_GAP_MINUTES = 45
STARTUP_LEAD_MINUTES = 5
MISSED_GRACE_MINUTES = 20
RETRY_MINUTES = 30
CHECK_INTERVAL_SECONDS = 30
BURST_WINDOW_SECONDS = 180


def _local_dt(day: date, clock: time) -> datetime:
    return MOSCOW_TZ.localize(datetime.combine(day, clock))


def _pick_legacy_daily_slots(day: date, *, now: datetime | None = None, rng=random) -> list[datetime]:
    """Compatibility path for old direct callers; production does not use this gap policy."""
    start = _local_dt(day, DAY_START)
    end = _local_dt(day, DAY_END)
    if now is not None and now.date() == day:
        start = max(start, now.astimezone(MOSCOW_TZ) + timedelta(minutes=STARTUP_LEAD_MINUTES))

    span_minutes = int((end - start).total_seconds() // 60)
    if span_minutes < 0:
        return []
    if span_minutes == 0:
        return [start]

    count = min(POSTS_PER_DAY, span_minutes // MIN_GAP_MINUTES + 1)
    if count <= 1:
        return [start + timedelta(minutes=rng.randint(0, span_minutes))]
    effective_gap = min(PREFERRED_GAP_MINUTES, span_minutes // (count - 1))
    slack = span_minutes - effective_gap * (count - 1)
    jitter = sorted(rng.randint(0, slack) for _ in range(count)) if slack > 0 else [0] * count
    offsets = [jitter[index] + index * effective_gap for index in range(count)]
    return [start + timedelta(minutes=offset) for offset in offsets]


def _pick_daily_slots(
    day: date,
    *,
    count: int | None = None,
    now: datetime | None = None,
    burst_chance: float = 0.0,
    rng=random,
) -> list[datetime]:
    """Mood-aware production slots may land anywhere in the Moscow calendar day."""
    if count is None:
        return _pick_legacy_daily_slots(day, now=now, rng=rng)
    if count <= 0:
        return []

    start = _local_dt(day, DAY_START)
    end = _local_dt(day, DAY_END)
    if now is not None and now.date() == day:
        start = max(start, now.astimezone(MOSCOW_TZ) + timedelta(minutes=STARTUP_LEAD_MINUTES))

    span_seconds = int((end - start).total_seconds())
    if span_seconds < 0:
        return []
    if span_seconds == 0:
        return [start for _ in range(count)]

    slots: list[datetime] = []
    for _ in range(count):
        if slots and rng.random() < burst_chance:
            base = rng.choice(slots)
            candidate = base + timedelta(seconds=rng.randint(0, BURST_WINDOW_SECONDS))
            candidate = min(max(candidate, start), end)
        else:
            candidate = start + timedelta(seconds=rng.randint(0, span_seconds))
        slots.append(candidate)

    return sorted(slots)


def _new_schedule(now: datetime, *, mood: dict | None = None, rng=random) -> dict:
    mood = mood or get_current_mood(rng=rng)
    target = daily_post_target(mood, rng=rng, default=NOMINAL_POSTS_PER_DAY)
    slots = _pick_daily_slots(
        now.date(),
        count=target,
        now=now,
        burst_chance=burst_probability(mood),
        rng=rng,
    )
    return {
        "date": now.date().isoformat(),
        "target_posts": target,
        "mood_name": mood.get("name"),
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
    span = int((latest - earliest).total_seconds())
    if span < 0:
        return None
    return earliest + timedelta(seconds=rng.randint(0, span))


def _repair_missed_slots(state: dict, now: datetime, rng=random) -> bool:
    """After a long restart, move stale slots forward instead of dumping them immediately."""
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


def _published_count(state: dict) -> int:
    return sum(1 for slot in state.get("slots", []) if slot.get("done") and not slot.get("missed"))


def _pending_slots(state: dict) -> list[dict]:
    return [slot for slot in state.get("slots", []) if not slot.get("done")]


def _reconcile_schedule_for_mood(state: dict, now: datetime, mood: dict, *, rng=random) -> bool:
    """Adjusts the remaining daily activity whenever Upupa's mood changes."""
    changed = False
    mood_name = mood.get("name")
    mood_changed = state.get("mood_name") != mood_name

    if mood_changed or not isinstance(state.get("target_posts"), int):
        selected = daily_post_target(mood, rng=rng, default=NOMINAL_POSTS_PER_DAY)
        state["target_posts"] = max(_published_count(state), selected)
        state["mood_name"] = mood_name
        changed = True

    target = int(state.get("target_posts") or NOMINAL_POSTS_PER_DAY)
    published = _published_count(state)
    required_pending = max(0, target - published)
    pending = sorted(_pending_slots(state), key=lambda slot: str(slot.get("at") or ""))

    if len(pending) > required_pending:
        remove_ids = {id(slot) for slot in pending[required_pending:]}
        state["slots"] = [slot for slot in state.get("slots", []) if id(slot) not in remove_ids]
        changed = True
    elif len(pending) < required_pending:
        missing = required_pending - len(pending)
        new_slots = _pick_daily_slots(
            now.date(),
            count=missing,
            now=now,
            burst_chance=burst_probability(mood),
            rng=rng,
        )
        for slot in new_slots:
            state.setdefault("slots", []).append({"at": slot.isoformat(), "done": False, "missed": False})
        if new_slots:
            changed = True

    state.setdefault("slots", []).sort(key=lambda slot: str(slot.get("at") or ""))
    return changed


def _top_up_schedule_slots(state: dict, now: datetime, rng=random) -> bool:
    """Compatibility migration for legacy five-slot schedules; production uses mood reconciliation."""
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
    mood = get_current_mood()
    state = load_schedule()
    if state.get("date") != now.date().isoformat():
        state = _new_schedule(now, mood=mood)
        save_schedule(state)
        logging.info(
            "[channel] daily slots mood=%s target=%s slots=%s",
            mood.get("name"),
            state.get("target_posts"),
            [slot["at"] for slot in state["slots"]],
        )
        return state

    changed = _repair_missed_slots(state, now)
    changed = _reconcile_schedule_for_mood(state, now, mood) or changed
    if changed:
        save_schedule(state)
        logging.info(
            "[channel] adjusted daily slots mood=%s target=%s slots=%s",
            mood.get("name"),
            state.get("target_posts"),
            [slot["at"] for slot in state["slots"]],
        )
    return state


async def channel_scheduler_loop(bot) -> None:
    """Background loop with mood-dependent activity and no enforced quiet hours or inter-post cooldown."""
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
