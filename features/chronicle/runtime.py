"""Production wiring for Chronicle without creating parallel history storage."""

from __future__ import annotations

import asyncio
import logging
import threading

from core.paths import HISTORY_DB_PATH, STATISTICS_DB_PATH
from features.chronicle import service
from infrastructure.persistence.sqlite_chronicle_backfill import SQLiteChronicleBackfillStore
from infrastructure.persistence.sqlite_chronicle_candidates import SQLiteChronicleCandidateStore
from infrastructure.persistence.sqlite_chronicle_events import SQLiteChronicleEventStore


_init_lock = threading.Lock()
_initialized = False
_background_task: asyncio.Task | None = None


def ensure_initialized() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        service.configure_chronicle(
            SQLiteChronicleCandidateStore(HISTORY_DB_PATH, STATISTICS_DB_PATH),
            SQLiteChronicleEventStore(HISTORY_DB_PATH),
            SQLiteChronicleBackfillStore(HISTORY_DB_PATH),
        )
        service.init_db()
        _initialized = True


def _task_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        logging.error("[chronicle] background task stopped: %s", exc, exc_info=exc)


def ensure_background_loop() -> None:
    global _background_task
    ensure_initialized()
    if _background_task is not None and not _background_task.done():
        return
    _background_task = asyncio.create_task(service.chronicle_loop(), name="chronicle-finalizer")
    _background_task.add_done_callback(_task_done)


async def capture_message(message):
    ensure_background_loop()
    return await service.capture_message(message)


async def capture_reaction(update):
    ensure_background_loop()
    return await service.capture_reaction(update)


async def capture_reaction_count(update):
    ensure_background_loop()
    return await service.capture_reaction_count(update)


async def request_backfill(chat_id: int):
    """Request initial indexing and return its current persistent status."""
    ensure_background_loop()
    await service.request_backfill(chat_id)
    return await asyncio.to_thread(service._stores()[2].state, int(chat_id))


async def list_events(chat_id: int, **kwargs):
    ensure_initialized()
    return await service.list_events(chat_id, **kwargs)


async def register_external_candidate(**kwargs):
    ensure_background_loop()
    return await service.register_external_candidate(**kwargs)
