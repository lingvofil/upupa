"""Automatic maintenance for the derived history index.

The recovery source ``user_messages.log`` is intentionally never truncated or
rewritten here. Retention is a logical deletion recorded by
``ManagedHistoryRepository``; periodic VACUUM only compacts ``history.db``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from core.history_store import get_history_repository
from core.logging_setup import logger
from core.paths import USER_MESSAGES_LOG_PATH
from core.time_utils import app_now, as_app_datetime, history_timestamp


HISTORY_RETENTION_DAYS = 180
MAINTENANCE_HOUR = 4
MAINTENANCE_MINUTE = 15
MAINTENANCE_RETRY_SECONDS = 15 * 60
MAINTENANCE_MAX_ATTEMPTS = 3


def next_maintenance_at(now: datetime | None = None) -> datetime:
    """Return the next 04:15 application-time maintenance boundary."""
    reference = as_app_datetime(now or app_now())
    target = reference.replace(
        hour=MAINTENANCE_HOUR,
        minute=MAINTENANCE_MINUTE,
        second=0,
        microsecond=0,
    )
    if target <= reference:
        target += timedelta(days=1)
    return target


def should_compact(now: datetime) -> bool:
    """Run the expensive VACUUM only on the first Sunday of a month."""
    reference = as_app_datetime(now)
    return reference.weekday() == 6 and reference.day <= 7


def run_history_maintenance_once(repository, *, now: datetime | None = None) -> dict:
    """Apply retention and, rarely, reclaim free SQLite pages."""
    reference = as_app_datetime(now or app_now())
    cutoff = reference - timedelta(days=HISTORY_RETENTION_DAYS)
    journal_before = repository.log_path.stat().st_size if repository.log_path.exists() else 0

    deleted = repository.prune_older_than(
        HISTORY_RETENTION_DAYS,
        now=reference,
    )
    compacted = False
    if deleted > 0 and should_compact(reference):
        repository.compact()
        compacted = True

    journal_after = repository.log_path.stat().st_size if repository.log_path.exists() else 0
    if journal_after != journal_before:
        raise RuntimeError("history maintenance unexpectedly changed user_messages.log")

    return {
        "deleted": int(deleted),
        "compacted": compacted,
        "cutoff": history_timestamp(cutoff),
        "journal_bytes": journal_after,
    }


async def history_maintenance_loop() -> None:
    """Run retention daily outside peak hours with bounded retries."""
    while True:
        target = next_maintenance_at()
        delay = max(1.0, (target - app_now()).total_seconds())
        logger.info(
            "History maintenance scheduled for %s retention_days=%d",
            target.isoformat(timespec="minutes"),
            HISTORY_RETENTION_DAYS,
        )
        await asyncio.sleep(delay)

        for attempt in range(1, MAINTENANCE_MAX_ATTEMPTS + 1):
            try:
                repository = get_history_repository(USER_MESSAGES_LOG_PATH)
                if repository is None:
                    raise RuntimeError("history repository is not configured")
                result = await asyncio.to_thread(
                    run_history_maintenance_once,
                    repository,
                    now=app_now(),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                if attempt >= MAINTENANCE_MAX_ATTEMPTS:
                    raise
                logger.exception(
                    "History maintenance failed attempt=%d/%d; retrying in %ds",
                    attempt,
                    MAINTENANCE_MAX_ATTEMPTS,
                    MAINTENANCE_RETRY_SECONDS,
                )
                await asyncio.sleep(MAINTENANCE_RETRY_SECONDS)
                continue

            logger.info(
                "History maintenance complete cutoff=%s deleted=%d compacted=%s "
                "journal_bytes=%d journal_untouched=true",
                result["cutoff"],
                result["deleted"],
                result["compacted"],
                result["journal_bytes"],
            )
            break
