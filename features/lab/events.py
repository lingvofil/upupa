"""Журнал событий экспериментов в JSONL.

Одна строка — одно событие:

    {"v": 1, "ts": "2026-11-07T18:04:05.123+00:00", "exp": "oracle",
     "event": "finish", "chat_id": -100123, "user_id": 42,
     "session": "a1b2c3d4e5f6", "data": {"hits": 14, "rounds": 20, "duration_s": 95}}

Базовые события: ``start``, ``action``, ``finish``, ``abandon``. Эксперимент
может добавлять свои (``vote``, ``purchase``…), но итог раунда обязан попасть
в ``finish`` или ``abandon``: задним числом его не восстановить. Длительность
кладём в ``data.duration_s``, если игра её знает; иначе её считают по ``ts``
событий одной ``session``.

Запись не ломает игру: ошибки логируются и глотаются. Текст сообщений сюда
сам по себе не попадает — только то, что эксперимент явно положил в ``data``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import LAB_EVENTS_PATH

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STANDARD_EVENTS = frozenset({"start", "action", "finish", "abandon"})


def new_session_id() -> str:
    """Короткий id партии/раунда для связки событий между собой."""
    return uuid.uuid4().hex[:12]


def build_event(
    experiment: str,
    event: str,
    *,
    chat_id: int | None,
    user_id: int | None = None,
    session_id: str | None = None,
    data: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not experiment or not event:
        raise ValueError("experiment и event обязательны")
    moment = now or datetime.now(timezone.utc)
    return {
        "v": SCHEMA_VERSION,
        "ts": moment.isoformat(timespec="milliseconds"),
        "exp": experiment,
        "event": event,
        "chat_id": chat_id,
        "user_id": user_id,
        "session": session_id,
        "data": dict(data) if data else {},
    }


class EventLog:
    """Append-only JSONL-файл. Одна строка пишется одним вызовом под замком."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(line)


def read_events(path: str | Path) -> Iterator[dict[str, Any]]:
    """Прочитать журнал, пропуская битые строки (например, оборванный хвост)."""
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


_event_log = EventLog(LAB_EVENTS_PATH)


def get_event_log() -> EventLog:
    return _event_log


def configure_event_log(log: EventLog) -> None:
    """Подменить журнал (тесты, другой путь)."""
    global _event_log
    _event_log = log


async def log_event(
    experiment: str,
    event: str,
    *,
    chat_id: int | None,
    user_id: int | None = None,
    session_id: str | None = None,
    **data: Any,
) -> None:
    """Записать событие вне event loop. Никогда не бросает исключений."""
    try:
        # Время фиксируем в момент вызова, а не когда освободится поток.
        record = build_event(
            experiment,
            event,
            chat_id=chat_id,
            user_id=user_id,
            session_id=session_id,
            data=data,
        )
        await asyncio.to_thread(_event_log.append, record)
    except Exception:
        logger.warning("[lab] событие %s/%s не записано", experiment, event, exc_info=True)
