"""Long-term pair dynamics from the lifetime daily activity table.

``social_relationship_daily`` is a real per-day time series that survives the
90-day raw-interaction retention.  This module turns it into things people can
read at a glance: a weekly sparkline, the current phase and a few deterministic
milestones (pauses, streaks, the busiest week).  No AI, no message text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
from typing import Any


SPARK_CHARS = "▁▂▃▄▅▆▇█"
SPARK_EMPTY = "·"
SPARK_WEEKS = 12
PAUSE_MIN_DAYS = 21
STREAK_MIN_DAYS = 5
PEAK_MIN_EVENTS = 8
MAX_MILESTONES = 4


@dataclass(frozen=True, slots=True)
class WeekBucket:
    start: date
    events: int


@dataclass(frozen=True, slots=True)
class DynamicsMilestone:
    day: date
    kind: str
    title: str
    summary: str
    priority: int


@dataclass(frozen=True, slots=True)
class RelationshipDynamics:
    weeks: tuple[WeekBucket, ...]
    sparkline: str
    phase: str
    total_events: int
    active_days: int
    first_day: date | None
    last_day: date | None
    milestones: tuple[DynamicsMilestone, ...]


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _parse_day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _daily_counts(rows: Iterable[Mapping[str, Any]]) -> dict[date, int]:
    counts: dict[date, int] = {}
    for row in rows:
        day = _parse_day(row.get("activity_date"))
        if day is None:
            continue
        events = max(0, int(row.get("event_count") or 0))
        if events:
            counts[day] = counts.get(day, 0) + events
    return counts


def weekly_buckets(counts: Mapping[date, int], *, today: date, weeks: int | None = None) -> list[WeekBucket]:
    """Continuous weekly series ending at the current week (zeros included)."""
    end = _week_start(today)
    if weeks is not None:
        start = end - timedelta(weeks=max(1, weeks) - 1)
    elif counts:
        start = _week_start(min(counts))
    else:
        start = end
    totals: dict[date, int] = {}
    for day, events in counts.items():
        week = _week_start(day)
        if start <= week <= end:
            totals[week] = totals.get(week, 0) + events
    result = []
    current = start
    while current <= end:
        result.append(WeekBucket(current, totals.get(current, 0)))
        current += timedelta(weeks=1)
    return result


def sparkline(values: Iterable[int]) -> str:
    values = list(values)
    peak = max(values, default=0)
    if peak <= 0:
        return SPARK_EMPTY * len(values)
    chars = []
    for value in values:
        if value <= 0:
            chars.append(SPARK_EMPTY)
            continue
        index = min(len(SPARK_CHARS) - 1, max(0, math.ceil(value / peak * len(SPARK_CHARS)) - 1))
        chars.append(SPARK_CHARS[index])
    return "".join(chars)


def current_phase(all_weeks: list[WeekBucket], counts: Mapping[date, int], *, today: date) -> str:
    if not counts:
        return "пока без истории"
    first_day = min(counts)
    last_day = max(counts)
    if (today - first_day).days <= 14:
        return "знакомятся"
    if (today - last_day).days >= 28:
        return "затишье"

    values = [bucket.events for bucket in all_weeks]
    recent = values[-4:]
    previous = values[-12:-4]
    recent_avg = sum(recent) / max(1, len(recent))
    previous_avg = sum(previous) / max(1, len(previous))
    peak = max(values, default=0)

    if peak >= PEAK_MIN_EVENTS and max(recent, default=0) == peak and recent_avg >= previous_avg:
        return "на пике"
    if sum(recent) >= 6 and recent_avg >= max(1.0, previous_avg) * 1.6:
        return "на подъёме"
    if previous_avg >= 2 and recent_avg <= previous_avg * 0.5:
        return "остывают"
    return "ровно"


def _plural(value: int, one: str, few: str, many: str) -> str:
    tail = value % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def _days(value: int) -> str:
    return f"{value} {_plural(value, 'день', 'дня', 'дней')}"


def _milestones(counts: Mapping[date, int], all_weeks: list[WeekBucket]) -> list[DynamicsMilestone]:
    if not counts:
        return []
    days = sorted(counts)
    items: list[DynamicsMilestone] = [
        DynamicsMilestone(
            day=days[0],
            kind="dynamics_first",
            title="Первый зафиксированный контакт",
            summary="С этого дня бот видит их взаимодействия.",
            priority=15,
        )
    ]

    pauses = []
    for previous, current in zip(days, days[1:]):
        gap = (current - previous).days
        if gap >= PAUSE_MIN_DAYS:
            pauses.append((gap, current))
    for gap, resumed in sorted(pauses, reverse=True)[:2]:
        items.append(
            DynamicsMilestone(
                day=resumed,
                kind="dynamics_comeback",
                title="Возвращение после паузы",
                summary=f"Снова на связи после {_days(gap)} тишины.",
                priority=45,
            )
        )

    best_len = 0
    best_end: date | None = None
    run = 1
    for previous, current in zip(days, days[1:]):
        run = run + 1 if (current - previous).days == 1 else 1
        if run > best_len:
            best_len, best_end = run, current
    if best_len >= STREAK_MIN_DAYS and best_end is not None:
        items.append(
            DynamicsMilestone(
                day=best_end,
                kind="dynamics_streak",
                title="Самая длинная серия",
                summary=f"{_days(best_len).capitalize()} подряд без перерыва.",
                priority=40,
            )
        )

    active_weeks = [bucket for bucket in all_weeks if bucket.events > 0]
    if len(active_weeks) >= 2:
        peak = max(active_weeks, key=lambda bucket: (bucket.events, bucket.start))
        if peak.events >= PEAK_MIN_EVENTS:
            items.append(
                DynamicsMilestone(
                    day=peak.start,
                    kind="dynamics_peak",
                    title="Самая плотная неделя",
                    summary=f"{peak.events} {_plural(peak.events, 'взаимодействие', 'взаимодействия', 'взаимодействий')} за неделю — личный рекорд пары.",
                    priority=50,
                )
            )

    items.sort(key=lambda item: (-item.priority, item.day))
    return sorted(items[:MAX_MILESTONES], key=lambda item: item.day)


def build_dynamics(rows: Iterable[Mapping[str, Any]], *, today: date | None = None) -> RelationshipDynamics:
    today = today or datetime.now(timezone.utc).date()
    counts = _daily_counts(rows)
    all_weeks = weekly_buckets(counts, today=today)
    recent_weeks = weekly_buckets(counts, today=today, weeks=SPARK_WEEKS)
    return RelationshipDynamics(
        weeks=tuple(recent_weeks),
        sparkline=sparkline(bucket.events for bucket in recent_weeks),
        phase=current_phase(all_weeks, counts, today=today),
        total_events=sum(counts.values()),
        active_days=len(counts),
        first_day=min(counts) if counts else None,
        last_day=max(counts) if counts else None,
        milestones=tuple(_milestones(counts, all_weeks)),
    )
