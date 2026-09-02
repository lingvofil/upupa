"""Persistence for active Crocodile drawing sessions.

The game itself keeps hot state in memory for fast Socket.IO access. This module
periodically snapshots only the durable fields to a root-level runtime JSON file
and restores them on startup. The persistence loop also flushes once on graceful
shutdown, so deploys and service restarts do not drop an active game.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path

from core.paths import CROCODILE_STATE_PATH
from games import crocodile


STATE_VERSION = 1
PERSIST_INTERVAL_SECONDS = 0.5
_last_payload: str | None = None


def _state_path() -> Path:
    return Path(CROCODILE_STATE_PATH)


def _session_to_record(chat_id: str, session: dict) -> dict:
    image = session.get("last_preview_bytes")
    if not isinstance(image, (bytes, bytearray)) or not image:
        image = base64.b64decode(crocodile.BLANK_PNG_B64)

    return {
        "chat_id": str(chat_id),
        "word": str(session["word"]),
        "drawer_id": int(session["drawer_id"]),
        "drawer_name": str(session.get("drawer_name") or "Художник"),
        "preview_message_id": int(session["preview_message_id"]),
        "last_preview_bytes_b64": base64.b64encode(bytes(image)).decode("ascii"),
    }


def _session_from_record(record: dict) -> tuple[str, dict]:
    chat_id = str(int(record["chat_id"]))
    if chat_id == "0":
        raise ValueError("invalid chat id")

    word = str(record["word"]).strip()
    if not word:
        raise ValueError("missing word")

    drawer_id = int(record["drawer_id"])
    if drawer_id <= 0:
        raise ValueError("invalid drawer id")

    preview_message_id = int(record["preview_message_id"])
    if preview_message_id <= 0:
        raise ValueError("invalid preview message id")

    encoded_image = str(record.get("last_preview_bytes_b64") or "")
    if encoded_image:
        image = base64.b64decode(encoded_image, validate=True)
    else:
        image = base64.b64decode(crocodile.BLANK_PNG_B64)
    if not image:
        raise ValueError("empty preview image")

    return chat_id, {
        "word": word,
        "drawer_id": drawer_id,
        "drawer_name": str(record.get("drawer_name") or "Художник"),
        "preview_message_id": preview_message_id,
        # A restored client should be allowed to publish a fresh snapshot
        # immediately instead of inheriting an old throttle timestamp.
        "last_preview_time": 0,
        "last_preview_bytes": image,
        "bump_task": None,
    }


def _serialize_current_state() -> str:
    records = []
    for chat_id, session in sorted(crocodile.game_sessions.items()):
        try:
            records.append(_session_to_record(chat_id, session))
        except Exception:
            logging.exception(
                "[crocodile] failed to serialize session chat=%s", chat_id
            )

    payload = {
        "version": STATE_VERSION,
        "sessions": records,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def persist_crocodile_sessions(*, force: bool = False) -> bool:
    """Atomically persist active sessions when their durable state changed."""
    global _last_payload

    payload = _serialize_current_state()
    if not force and payload == _last_payload:
        return False

    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(payload, encoding="utf-8")
    temp_path.replace(path)
    _last_payload = payload
    return True


def restore_crocodile_sessions() -> int:
    """Restore active sessions and their preview bump tasks after restart."""
    global _last_payload

    path = _state_path()
    if not path.exists():
        _last_payload = None
        return 0

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("[crocodile] state restore failed path=%s", path)
        return 0

    if payload.get("version") != STATE_VERSION:
        logging.warning(
            "[crocodile] unsupported state version=%r path=%s",
            payload.get("version"),
            path,
        )
        return 0

    restored_sessions: dict[str, dict] = {}
    for record in payload.get("sessions", []):
        try:
            chat_id, session = _session_from_record(record)
            restored_sessions[chat_id] = session
        except Exception:
            logging.exception(
                "[crocodile] session restore failed record=%r", record
            )

    crocodile.game_sessions.clear()
    crocodile.game_sessions.update(restored_sessions)

    for chat_id, session in restored_sessions.items():
        if crocodile.BUMP_INTERVAL and crocodile.BUMP_INTERVAL > 0:
            try:
                session["bump_task"] = crocodile._start_background_task(
                    crocodile._bump_loop(chat_id),
                    name=f"crocodile-bump:{chat_id}:restored",
                )
            except Exception:
                logging.exception(
                    "[crocodile] failed to restore bump task chat=%s", chat_id
                )

    # Normalize the state file after skipping any malformed records.
    _last_payload = None
    persist_crocodile_sessions(force=True)
    if restored_sessions:
        logging.info(
            "[crocodile] restored active sessions=%s", len(restored_sessions)
        )
    return len(restored_sessions)


async def crocodile_session_persistence_loop() -> None:
    """Persist changed session state and flush once more on graceful shutdown."""
    try:
        while True:
            try:
                persist_crocodile_sessions()
            except Exception:
                logging.exception("[crocodile] periodic state persistence failed")
            await asyncio.sleep(PERSIST_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        try:
            persist_crocodile_sessions(force=True)
        except Exception:
            logging.exception("[crocodile] shutdown state persistence failed")
        raise
