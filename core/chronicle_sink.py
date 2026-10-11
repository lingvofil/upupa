"""Decoupled hook for reporting notable moments to the chat Chronicle.

Games and features (court, Crocodile, DnD …) call ``report_chronicle_event``
without importing the Chronicle itself.  The application wires the real sink
at startup; until then (and in tests) reports are silently dropped.  Delivery
is fire-and-forget and can never break the caller.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import Any


ChronicleSink = Callable[..., Awaitable[Any]]

_sink: ChronicleSink | None = None
_pending: set[asyncio.Task] = set()


def configure_chronicle_sink(sink: ChronicleSink | None) -> None:
    global _sink
    _sink = sink


def chronicle_participant(user_id: int, name: str | None, username: str | None = None) -> dict:
    return {"id": int(user_id), "name": (name or "Участник").strip() or "Участник", "username": username}


async def _deliver(sink: ChronicleSink, payload: dict[str, Any]) -> None:
    try:
        await sink(**payload)
    except Exception as exc:
        logging.warning("[chronicle] external report failed source=%s: %s", payload.get("source"), exc)


def report_chronicle_event(
    *,
    chat_id: int,
    context: str,
    source: str,
    participants: list[dict] | None = None,
    source_message_ids: list[int] | None = None,
    base_score: float | None = None,
    anchor_message_id: int | None = None,
) -> bool:
    """Queue a Chronicle candidate; returns False when nothing was queued."""
    sink = _sink
    if sink is None or not str(context or "").strip():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    payload = {
        "chat_id": int(chat_id),
        "context": str(context).strip(),
        "participants": list(participants or []),
        "source_message_ids": [int(item) for item in source_message_ids or []],
        "source": str(source),
        "base_score": base_score,
        "anchor_message_id": anchor_message_id,
    }
    task = loop.create_task(_deliver(sink, payload), name=f"chronicle-report:{source}")
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return True
