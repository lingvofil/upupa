"""Automatic maintenance for the derived history index.

The recovery source ``user_messages.log`` is intentionally never truncated or
rewritten here. Retention is a logical deletion recorded by
``ManagedHistoryRepository``; periodic VACUUM only compacts ``history.db``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import hashlib
import os
from pathlib import Path

from core.history_store import get_history_repository
from core.logging_setup import logger
from core.paths import USER_MESSAGES_LOG_PATH
from core.time_utils import app_now, as_app_datetime, history_timestamp


HISTORY_RETENTION_DAYS = 180
MAINTENANCE_HOUR = 4
MAINTENANCE_MINUTE = 15
MAINTENANCE_RETRY_SECONDS = 15 * 60
MAINTENANCE_MAX_ATTEMPTS = 3
JOURNAL_ANCHOR_BYTES = 4096


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


def _journal_anchor(path: Path):
    """Snapshot the immutable end of the current journal prefix."""
    if not path.exists():
        return None
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        anchor_start = max(0, stat.st_size - JOURNAL_ANCHOR_BYTES)
        stream.seek(anchor_start)
        digest = hashlib.sha256(stream.read(stat.st_size - anchor_start)).hexdigest()
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        anchor_start,
        digest,
    )


def _verify_journal_prefix(path: Path, anchor) -> int:
    """Allow concurrent appends but reject replacement, truncation or rewrites."""
    if anchor is None:
        return path.stat().st_size if path.exists() else 0
    if not path.exists():
        raise RuntimeError("history maintenance lost user_messages.log")

    expected_dev, expected_ino, expected_size, anchor_start, expected_digest = anchor
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        if (stat.st_dev, stat.st_ino) != (expected_dev, expected_ino):
            raise RuntimeError("history maintenance replaced user_messages.log")
        if stat.st_size < expected_size:
            raise RuntimeError("history maintenance truncated user_messages.log")
        stream.seek(anchor_start)
        digest = hashlib.sha256(stream.read(expected_size - anchor_start)).hexdigest()
    if digest != expected_digest:
        raise RuntimeError("history maintenance rewrote user_messages.log")
    return stat.st_size


def run_history_maintenance_once(repository, *, now: datetime | None = None) -> dict:
    """Apply retention and, rarely, reclaim free SQLite pages."""
    reference = as_app_datetime(now or app_now())
    cutoff = reference - timedelta(days=HISTORY_RETENTION_DAYS)
    journal_path = Path(repository.log_path)
    journal_before = _journal_anchor(journal_path)

    deleted = repository.prune_older_than(
        HISTORY_RETENTION_DAYS,
        now=reference,
    )
    compacted = False
    if deleted > 0 and should_compact(reference):
        repository.compact()
        compacted = True

    journal_bytes = _verify_journal_prefix(journal_path, journal_before)
    return {
        "deleted": int(deleted),
        "compacted": compacted,
        "cutoff": history_timestamp(cutoff),
        "journal_bytes": journal_bytes,
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
                "journal_bytes=%d journal_append_only=true",
                result["cutoff"],
                result["deleted"],
                result["compacted"],
                result["journal_bytes"],
            )
            break
