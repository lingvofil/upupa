"""Durable active-session state for every reverse Crocodile mode."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from pathlib import Path

from core.paths import CROCODILE_STATE_PATH
from games import crocodile
from games import reverse_crocodile as reverse
from games import reverse_crocodile_modes as modes
from games.reverse_crocodile_phrases import normalize_mode


STATE_VERSION = 1
REVERSE_STATE_PATH = Path(CROCODILE_STATE_PATH).with_name(
    "reverse_crocodile_state.json"
)
_VALID_MODES = {"word", "reveal", "movie", "cartoon", "proverbs", "pun"}
_last_payload: str | None = None
_restored = False


def _wall_time_for_monotonic(
    session: dict,
    source_key: str,
    *,
    marker_key: str,
    wall_key: str,
    now_monotonic: float,
    now_wall: float,
) -> float | None:
    """Map a monotonic event time to a stable wall-clock timestamp.

    The derived value is cached on the hot session and recomputed only when the
    source monotonic timestamp changes. This keeps the serialized payload stable
    between real state changes instead of rewriting the file every persistence
    tick just because a countdown is running.
    """
    source = session.get(source_key)
    if source is None:
        session.pop(marker_key, None)
        session.pop(wall_key, None)
        return None
    source_value = float(source)
    cached_marker = session.get(marker_key)
    cached_wall = session.get(wall_key)
    if cached_marker != source_value or not isinstance(cached_wall, (int, float)):
        elapsed = max(0.0, now_monotonic - source_value)
        cached_wall = now_wall - elapsed
        session[marker_key] = source_value
        session[wall_key] = cached_wall
    return float(cached_wall)


def _restore_monotonic_time(
    wall_time: object,
    *,
    now_monotonic: float,
    now_wall: float,
) -> float:
    event_wall = float(wall_time)
    elapsed = max(0.0, now_wall - event_wall)
    return now_monotonic - elapsed


def _session_to_record(
    chat_id: str,
    session: dict,
    *,
    now_monotonic: float,
    now_wall: float,
) -> dict:
    word = str(session.get("word") or "").strip()
    if not word:
        raise ValueError("missing reverse Crocodile answer")

    image = session.get("image")
    if not isinstance(image, (bytes, bytearray)) or not image:
        raise ValueError("missing reverse Crocodile image")

    message_id = int(session.get("message_id") or 0)
    if message_id <= 0:
        raise ValueError("reverse Crocodile round has not been published yet")

    mode = normalize_mode(str(session.get("mode") or "word"))
    if mode not in _VALID_MODES:
        raise ValueError(f"unsupported reverse Crocodile mode: {mode}")

    started_wall_time = _wall_time_for_monotonic(
        session,
        "started_at",
        marker_key="_persist_started_source",
        wall_key="_persist_started_wall_time",
        now_monotonic=now_monotonic,
        now_wall=now_wall,
    )
    if started_wall_time is None:
        raise ValueError("missing reverse Crocodile start time")
    last_hint_wall_time = _wall_time_for_monotonic(
        session,
        "last_hint_at",
        marker_key="_persist_hint_source",
        wall_key="_persist_hint_wall_time",
        now_monotonic=now_monotonic,
        now_wall=now_wall,
    )

    return {
        "chat_id": str(int(chat_id)),
        "word": word,
        "difficulty": str(session.get("difficulty") or "medium"),
        "mode": mode,
        "image_b64": base64.b64encode(bytes(image)).decode("ascii"),
        "message_id": message_id,
        "hints": max(0, int(session.get("hints") or 0)),
        "revealed_positions": sorted(
            int(index) for index in session.get("revealed_positions", set())
        ),
        "revealed_tiles": max(0, int(session.get("revealed_tiles") or 0)),
        "reveal_order": [int(index) for index in session.get("reveal_order", [])],
        "started_wall_time": started_wall_time,
        "last_hint_wall_time": last_hint_wall_time,
    }


def _session_from_record(
    record: dict,
    *,
    now_monotonic: float,
    now_wall: float,
) -> tuple[str, dict]:
    chat_id = str(int(record["chat_id"]))
    if chat_id == "0":
        raise ValueError("invalid chat id")

    word = str(record["word"]).strip()
    if not word:
        raise ValueError("missing answer")

    mode = normalize_mode(str(record.get("mode") or "word"))
    if mode not in _VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")

    image = base64.b64decode(str(record["image_b64"]), validate=True)
    if not image:
        raise ValueError("empty image")

    message_id = int(record["message_id"])
    if message_id <= 0:
        raise ValueError("invalid message id")

    started_wall_time = float(record["started_wall_time"])
    started_at = _restore_monotonic_time(
        started_wall_time,
        now_monotonic=now_monotonic,
        now_wall=now_wall,
    )

    last_hint_wall_time = record.get("last_hint_wall_time")
    last_hint_at = None
    if last_hint_wall_time is not None:
        last_hint_wall_time = float(last_hint_wall_time)
        last_hint_at = _restore_monotonic_time(
            last_hint_wall_time,
            now_monotonic=now_monotonic,
            now_wall=now_wall,
        )

    revealed_positions = {
        int(index) for index in (record.get("revealed_positions") or [])
    }
    reveal_order = [int(index) for index in (record.get("reveal_order") or [])]

    session = {
        "word": word,
        "difficulty": str(record.get("difficulty") or "medium"),
        "mode": mode,
        "image": image,
        "message_id": message_id,
        "started_at": started_at,
        "mode_task": None,
        "round_task": None,
        "hints": max(0, int(record.get("hints") or 0)),
        "revealed_positions": revealed_positions,
        "hint_lock": asyncio.Lock(),
        "last_hint_at": last_hint_at,
        "revealed_tiles": max(0, int(record.get("revealed_tiles") or 0)),
        "reveal_order": reveal_order,
        "_persist_started_source": started_at,
        "_persist_started_wall_time": started_wall_time,
    }
    if last_hint_at is not None:
        session["_persist_hint_source"] = last_hint_at
        session["_persist_hint_wall_time"] = last_hint_wall_time
    return chat_id, session


def _serialize_current_state() -> str:
    records = []
    now_monotonic = time.monotonic()
    now_wall = time.time()
    for chat_id, session in sorted(reverse.games.items()):
        # start_game/start_mode publish the Telegram card after installing the
        # in-memory session. Do not persist that tiny incomplete window.
        if not session.get("message_id"):
            continue
        try:
            records.append(
                _session_to_record(
                    chat_id,
                    session,
                    now_monotonic=now_monotonic,
                    now_wall=now_wall,
                )
            )
        except Exception:
            logging.exception(
                "[rcroc-state] failed to serialize session chat=%s", chat_id
            )
    return json.dumps(
        {"version": STATE_VERSION, "sessions": records},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def persist_reverse_crocodile_sessions(*, force: bool = False) -> bool:
    """Atomically snapshot all active reverse Crocodile rounds."""
    global _last_payload

    payload = _serialize_current_state()
    if not force and payload == _last_payload:
        return False

    REVERSE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = REVERSE_STATE_PATH.with_suffix(REVERSE_STATE_PATH.suffix + ".tmp")
    temp_path.write_text(payload, encoding="utf-8")
    temp_path.replace(REVERSE_STATE_PATH)
    _last_payload = payload
    return True


def _start_restored_task(coro, *, name: str):
    try:
        return crocodile._start_background_task(coro, name=name)
    except Exception:
        coro.close()
        raise


def _resume_session_tasks(chat_id: str, session: dict) -> None:
    mode = normalize_mode(str(session.get("mode") or "word"))
    if mode == "word":
        session["round_task"] = _start_restored_task(
            reverse._round_loop(chat_id, session),
            name=f"reverse-crocodile-round:{chat_id}:restored",
        )
        return

    if (
        mode == "reveal"
        and int(session.get("revealed_tiles") or 0)
        < modes.REVEAL_COLS * modes.REVEAL_ROWS
    ):
        session["mode_task"] = _start_restored_task(
            modes._reveal_loop(chat_id, session),
            name=f"reverse-croc-reveal:{chat_id}:restored",
        )
    session["round_task"] = _start_restored_task(
        modes._round_loop(chat_id, session),
        name=f"reverse-croc-mode-round:{chat_id}:restored",
    )


def restore_reverse_crocodile_sessions() -> int:
    """Restore reverse Crocodile sessions and resume their timers/tasks."""
    global _last_payload, _restored

    if _restored:
        return len(reverse.games)
    _restored = True
    if not REVERSE_STATE_PATH.is_file():
        _last_payload = None
        return 0

    try:
        payload = json.loads(REVERSE_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
            raise ValueError("unsupported reverse Crocodile state")
    except Exception:
        logging.exception(
            "[rcroc-state] failed to read state path=%s", REVERSE_STATE_PATH
        )
        return 0

    restored: dict[str, dict] = {}
    now_monotonic = time.monotonic()
    now_wall = time.time()
    for record in payload.get("sessions") or []:
        try:
            chat_id, session = _session_from_record(
                record,
                now_monotonic=now_monotonic,
                now_wall=now_wall,
            )
            restored[chat_id] = session
        except Exception:
            logging.exception(
                "[rcroc-state] failed to restore session record=%r", record
            )

    reverse.games.clear()
    reverse.games.update(restored)
    for chat_id, session in restored.items():
        try:
            _resume_session_tasks(chat_id, session)
        except Exception:
            logging.exception(
                "[rcroc-state] failed to resume session chat=%s", chat_id
            )

    _last_payload = None
    persist_reverse_crocodile_sessions(force=True)
    if restored:
        logging.info("[rcroc-state] restored active sessions=%s", len(restored))
    return len(restored)
