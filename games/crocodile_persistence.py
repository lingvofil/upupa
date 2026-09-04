"""Persistence and runtime guards for Crocodile.

The game itself keeps hot state in memory for fast Socket.IO access. This module
periodically snapshots the durable session fields to a root-level runtime JSON
file and restores them on startup. It also owns compatibility migration for
Crocodile runtime files that accidentally moved under ``games/`` during the
package-layout refactor, plus small runtime guards that must survive restarts.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
import random
import secrets
import time

from core.paths import CROCODILE_STATE_PATH
from games import crocodile


STATE_VERSION = 1
WORD_HISTORY_VERSION = 1
PERSIST_INTERVAL_SECONDS = 0.5
CROCODILE_BUMP_INTERVAL_SECONDS = 60
WORD_HISTORY_CARRYOVER = 20
CROCODILE_SCORES_PATH = Path(CROCODILE_STATE_PATH).with_name("crocodile_scores.json")
CROCODILE_WORD_HISTORY_PATH = Path(CROCODILE_STATE_PATH).with_name(
    "crocodile_word_history.json"
)
LEGACY_CROCODILE_SCORES_PATH = Path(crocodile.__file__).with_name(
    "crocodile_scores.json"
)
_last_payload: str | None = None

_original_pick_word = crocodile._pick_word
_original_stop_session = crocodile._stop_session
_original_authorize_socket_room = crocodile._authorize_socket_room
_runtime_guards_configured = False


def _state_path() -> Path:
    return Path(CROCODILE_STATE_PATH)


def _canvas_room_for_chat(chat_id: str | int) -> str:
    cid = str(chat_id)
    return f"m{cid[1:]}" if cid.startswith("-") else cid


def _ensure_runtime_session_token(session: dict) -> str:
    token = str(session.get("_runtime_session_token") or "")
    if not token:
        token = secrets.token_urlsafe(18)
        session["_runtime_session_token"] = token
    return token


async def _authorize_socket_room_for_current_round(sid, data, *, bind_room: bool = False):
    """Bind a socket to one concrete round, not just to a Telegram chat.

    The original authorization verifies the Telegram user and room. The extra
    token prevents a canvas left open from a previous round from becoming valid
    again when the same user happens to draw the next round in the same chat.
    """
    room, chat_id, session = await _original_authorize_socket_room(
        sid,
        data,
        bind_room=bind_room,
    )
    round_token = _ensure_runtime_session_token(session)
    socket_session = await crocodile.sio.get_session(sid)

    if bind_room:
        socket_session["crocodile_round_token"] = round_token
        await crocodile.sio.save_session(sid, socket_session)
    elif socket_session.get("crocodile_round_token") != round_token:
        raise crocodile.WebAppAuthError("socket belongs to an expired Crocodile round")

    return room, chat_id, session


async def _stop_session_and_close_canvas_room(chat_id: str, reason: str = ""):
    """Remove old canvas sockets from the chat room before ending the round."""
    room = _canvas_room_for_chat(chat_id)
    try:
        await crocodile.sio.close_room(room)
    except Exception:
        logging.exception(
            "[crocodile] failed to close stale canvas room=%s reason=%s",
            room,
            reason,
        )
    await _original_stop_session(chat_id, reason=reason)


def _load_word_history() -> list[str]:
    path = Path(CROCODILE_WORD_HISTORY_PATH)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("word history must be an object")
        if payload.get("version") != WORD_HISTORY_VERSION:
            return []
        used = payload.get("used", [])
        if not isinstance(used, list):
            raise ValueError("word history used must be a list")
        return [str(item) for item in used if str(item).strip()]
    except Exception:
        logging.exception("[crocodile] failed to load word history path=%s", path)
        return []


def _write_word_history(used: list[str]) -> None:
    path = Path(CROCODILE_WORD_HISTORY_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(
            {"version": WORD_HISTORY_VERSION, "used": used},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temp_path.replace(path)


def pick_crocodile_word() -> str:
    """Pick from a persistent shuffle-like cycle instead of ``random.choice``.

    Every unique normalized word is used once before the cycle can repeat. When
    the dictionary is exhausted, a new cycle starts while keeping the most recent
    words blocked, so the boundary between cycles cannot immediately repeat them.
    The history is global for the bot and persisted in the repository root, which
    also means the regular deploy backup captures it automatically.
    """
    words = crocodile._load_words()
    unique_words: dict[str, str] = {}
    for raw_word in words:
        key = crocodile._normalize_guess(str(raw_word))
        if key and key not in unique_words:
            unique_words[key] = str(raw_word)

    if not unique_words:
        return _original_pick_word()

    used = _load_word_history()
    cleaned_used: list[str] = []
    seen: set[str] = set()
    for key in used:
        if key in unique_words and key not in seen:
            cleaned_used.append(key)
            seen.add(key)
    used = cleaned_used

    available = [key for key in unique_words if key not in seen]
    if not available:
        carry_count = min(
            WORD_HISTORY_CARRYOVER,
            max(0, len(unique_words) - 1),
        )
        used = used[-carry_count:] if carry_count else []
        seen = set(used)
        available = [key for key in unique_words if key not in seen]

    chosen_key = random.choice(available)
    used.append(chosen_key)
    try:
        _write_word_history(used)
    except Exception:
        logging.exception(
            "[crocodile] failed to persist word history path=%s",
            CROCODILE_WORD_HISTORY_PATH,
        )
    return unique_words[chosen_key]


def configure_crocodile_runtime() -> None:
    """Apply production runtime paths/settings before handlers can run."""
    global _runtime_guards_configured

    crocodile.BUMP_INTERVAL = CROCODILE_BUMP_INTERVAL_SECONDS
    crocodile.SCORES_FILE = str(CROCODILE_SCORES_PATH)
    crocodile._pick_word = pick_crocodile_word

    if not _runtime_guards_configured:
        crocodile._stop_session = _stop_session_and_close_canvas_room
        crocodile._authorize_socket_room = _authorize_socket_room_for_current_round
        _runtime_guards_configured = True


def _normalize_scores_payload(raw: object) -> dict[str, dict[str, dict]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("score payload must be an object")

    normalized: dict[str, dict[str, dict]] = {}
    for cid, table in raw.items():
        if not isinstance(table, dict):
            continue
        chat_scores: dict[str, dict] = {}
        for uid, value in table.items():
            user_id = str(uid)
            if isinstance(value, int):
                chat_scores[user_id] = {"pts": int(value), "name": ""}
            elif isinstance(value, dict):
                chat_scores[user_id] = {
                    "pts": int(value.get("pts", 0)),
                    "name": str(value.get("name", "") or ""),
                }
        normalized[str(cid)] = chat_scores
    return normalized


def _read_scores(path: Path) -> dict[str, dict[str, dict]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _normalize_scores_payload(raw)


def _merge_score_tables(
    historical: dict[str, dict[str, dict]],
    misplaced: dict[str, dict[str, dict]],
) -> dict[str, dict[str, dict]]:
    """Merge the two score eras created by the June 2026 path split."""
    if historical == misplaced:
        return historical

    merged: dict[str, dict[str, dict]] = {}
    for source in (historical, misplaced):
        for chat_id, table in source.items():
            target_table = merged.setdefault(str(chat_id), {})
            for user_id, value in table.items():
                target = target_table.setdefault(
                    str(user_id),
                    {"pts": 0, "name": ""},
                )
                target["pts"] = int(target.get("pts", 0)) + int(
                    value.get("pts", 0)
                )
                name = str(value.get("name", "") or "")
                if name:
                    target["name"] = name
    return merged


def _write_scores(path: Path, scores: dict[str, dict[str, dict]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(scores, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def migrate_crocodile_scores() -> bool:
    """Restore the canonical root score file and merge the misplaced score era.

    Before the package refactor ``crocodile.py`` lived in the repository root,
    so ``dirname(__file__)/crocodile_scores.json`` pointed at the durable root
    file. After the module moved to ``games/`` the same expression silently
    started a second scoreboard. If both files exist, their points represent
    the two consecutive eras and are summed once. The misplaced file is then
    archived so the migration cannot run twice.
    """
    configure_crocodile_runtime()

    canonical = Path(CROCODILE_SCORES_PATH)
    legacy = Path(LEGACY_CROCODILE_SCORES_PATH)
    try:
        if canonical.resolve() == legacy.resolve() or not legacy.is_file():
            return False

        historical = _read_scores(canonical) if canonical.is_file() else {}
        misplaced = _read_scores(legacy)
        merged = _merge_score_tables(historical, misplaced)

        archive = legacy.with_name(
            f"{legacy.stem}.migrated-{time.time_ns()}{legacy.suffix}"
        )
        legacy.replace(archive)
        try:
            _write_scores(canonical, merged)
        except Exception:
            try:
                archive.replace(legacy)
            except Exception:
                logging.exception(
                    "[crocodile] failed to roll back score migration archive=%s",
                    archive,
                )
            raise

        crocodile._scores = merged
        logging.warning(
            "[crocodile] migrated score file %s -> %s; archive=%s",
            legacy,
            canonical,
            archive,
        )
        return True
    except Exception:
        logging.exception(
            "[crocodile] score migration failed canonical=%s legacy=%s",
            canonical,
            legacy,
        )
        return False


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

    configure_crocodile_runtime()
    migrate_crocodile_scores()

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
