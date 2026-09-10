"""Durable state for Crocodile party modes and duo metadata."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

from core.paths import CROCODILE_STATE_PATH
from games import crocodile, crocodile_modes
from games import crocodile_persistence as persistence


PARTY_STATE_VERSION = 1
PARTY_STATE_PATH = Path(CROCODILE_STATE_PATH).with_name("crocodile_party_state.json")
_TRANSIENT_KEYS = {"vote_task", "_canvas_token"}
_BYTES_MARKER = "__upupa_bytes_b64__"

_configured = False
_restored = False
_last_payload: str | None = None
_original_persist_sessions = None
_original_configure_runtime = None
_original_session_to_record = None
_original_session_from_record = None
_original_start_duel_vote = None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {_BYTES_MARKER: base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value)]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
            if str(key) not in _TRANSIENT_KEYS
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported party-state value: {type(value).__name__}")


def _from_json_safe(value: Any) -> Any:
    if isinstance(value, list):
        return [_from_json_safe(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {_BYTES_MARKER}:
            return base64.b64decode(str(value[_BYTES_MARKER]), validate=True)
        return {str(key): _from_json_safe(item) for key, item in value.items()}
    return value


def _party_payload() -> dict[str, Any]:
    canvases = {}
    for key, session in crocodile_modes.canvas_sessions.items():
        mode = str(session.get("mode") or "")
        parent = str(session.get("duel_chat_id") or session.get("telephone_chat_id") or "")
        if mode == "duel" and parent not in crocodile_modes.duel_games:
            continue
        if mode == "telephone" and parent not in crocodile_modes.telephone_games:
            continue
        if mode in {"duel", "telephone"}:
            canvases[str(key)] = session
    return {
        "version": PARTY_STATE_VERSION,
        "duels": crocodile_modes.duel_games,
        "telephones": crocodile_modes.telephone_games,
        "canvases": canvases,
    }


def persist_party_modes(*, force: bool = False) -> bool:
    global _last_payload
    payload = json.dumps(
        _json_safe(_party_payload()),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if not force and payload == _last_payload:
        return False
    PARTY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PARTY_STATE_PATH.with_suffix(PARTY_STATE_PATH.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(PARTY_STATE_PATH)
    _last_payload = payload
    return True


def _normalize_restored_duel(raw: dict) -> dict:
    duel = dict(raw)
    duel["artists"] = [
        (int(row[0]), str(row[1]))
        for row in duel.get("artists", [])
        if isinstance(row, (list, tuple)) and len(row) == 2 and int(row[0]) > 0
    ]
    duel["votes"] = {
        int(user_id): int(slot) for user_id, slot in (duel.get("votes") or {}).items()
    }
    duel["finished"] = {int(slot) for slot in duel.get("finished", [])}
    duel["images"] = {
        int(slot): image for slot, image in (duel.get("images") or {}).items()
    }
    return duel


def _normalize_restored_telephone(raw: dict) -> dict:
    game = dict(raw)
    game["players"] = [
        (int(row[0]), str(row[1]))
        for row in game.get("players", [])
        if isinstance(row, (list, tuple)) and len(row) == 2 and int(row[0]) > 0
    ]
    game["step"] = max(0, int(game.get("step") or 0))
    return game


async def _resume_duel_vote(chat_id: str, duel: dict) -> None:
    deadline = float(duel.get("vote_deadline") or 0)
    if deadline <= 0:
        deadline = time.time() + crocodile_modes.DUEL_VOTE_SECONDS
        duel["vote_deadline"] = deadline
    try:
        await asyncio.sleep(max(0.0, deadline - time.time()))
        await crocodile_modes._finish_duel_vote(chat_id, duel)
    except asyncio.CancelledError:
        return


async def _resume_pending_vote(chat_id: str, duel: dict) -> None:
    if (
        crocodile_modes.duel_games.get(chat_id) is duel
        and duel.get("phase") == "answer_resolved"
    ):
        await crocodile_modes._start_duel_vote(chat_id, duel)


def restore_party_modes() -> int:
    global _restored, _last_payload
    if _restored:
        return len(crocodile_modes.duel_games) + len(crocodile_modes.telephone_games)
    _restored = True
    if not PARTY_STATE_PATH.is_file():
        _last_payload = None
        return 0
    try:
        payload = _from_json_safe(json.loads(PARTY_STATE_PATH.read_text(encoding="utf-8")))
        if not isinstance(payload, dict) or payload.get("version") != PARTY_STATE_VERSION:
            raise ValueError("unsupported Crocodile party state")
    except Exception:
        logging.exception("[croc-party] failed to read state path=%s", PARTY_STATE_PATH)
        return 0

    crocodile_modes.duel_games.clear()
    crocodile_modes.telephone_games.clear()
    crocodile_modes.canvas_sessions.clear()

    for chat_id, raw in (payload.get("duels") or {}).items():
        try:
            duel = _normalize_restored_duel(raw)
            if duel.get("artists"):
                crocodile_modes.duel_games[str(chat_id)] = duel
        except Exception:
            logging.exception("[croc-party] failed to restore duel chat=%s", chat_id)
    for chat_id, raw in (payload.get("telephones") or {}).items():
        try:
            game = _normalize_restored_telephone(raw)
            if game.get("players"):
                crocodile_modes.telephone_games[str(chat_id)] = game
        except Exception:
            logging.exception("[croc-party] failed to restore telephone chat=%s", chat_id)
    for key, session in (payload.get("canvases") or {}).items():
        try:
            mode = str(session.get("mode") or "")
            parent = str(session.get("duel_chat_id") or session.get("telephone_chat_id") or "")
            if mode == "duel" and parent not in crocodile_modes.duel_games:
                continue
            if mode == "telephone" and parent not in crocodile_modes.telephone_games:
                continue
            session["last_preview_time"] = 0
            crocodile_modes.canvas_sessions[str(key)] = dict(session)
        except Exception:
            logging.exception("[croc-party] failed to restore canvas key=%s", key)

    for chat_id, duel in list(crocodile_modes.duel_games.items()):
        if duel.get("phase") == "voting":
            duel["vote_task"] = crocodile._start_background_task(
                _resume_duel_vote(chat_id, duel),
                name=f"crocodile-duel-vote:{chat_id}:restored",
            )
        elif duel.get("phase") == "answer_resolved":
            duel["vote_task"] = crocodile._start_background_task(
                _resume_pending_vote(chat_id, duel),
                name=f"crocodile-duel-vote-start:{chat_id}:restored",
            )

    _last_payload = None
    persist_party_modes(force=True)
    count = len(crocodile_modes.duel_games) + len(crocodile_modes.telephone_games)
    if count:
        logging.info("[croc-party] restored active party games=%s", count)
    return count


def _session_to_record_with_duo(chat_id: str, session: dict) -> dict:
    record = _original_session_to_record(chat_id, session)
    raw_ids = session.get("drawer_ids")
    if isinstance(raw_ids, (list, tuple, set)):
        ids = []
        for value in raw_ids:
            try:
                user_id = int(value)
            except (TypeError, ValueError):
                continue
            if user_id > 0 and user_id not in ids:
                ids.append(user_id)
        if ids:
            record["drawer_ids"] = ids
    raw_names = session.get("drawer_names")
    if isinstance(raw_names, list):
        names = [str(name) for name in raw_names if str(name).strip()]
        if names:
            record["drawer_names"] = names
    mode = str(session.get("mode") or "")
    if mode:
        record["mode"] = mode
    return record


def _session_from_record_with_duo(record: dict) -> tuple[str, dict]:
    chat_id, session = _original_session_from_record(record)
    raw_ids = record.get("drawer_ids")
    if isinstance(raw_ids, list):
        ids = []
        for value in raw_ids:
            try:
                user_id = int(value)
            except (TypeError, ValueError):
                continue
            if user_id > 0 and user_id not in ids:
                ids.append(user_id)
        primary = int(session.get("drawer_id") or 0)
        if primary > 0 and primary not in ids:
            ids.insert(0, primary)
        if ids:
            session["drawer_ids"] = ids
    raw_names = record.get("drawer_names")
    if isinstance(raw_names, list):
        names = [str(name) for name in raw_names if str(name).strip()]
        if names:
            session["drawer_names"] = names
    mode = str(record.get("mode") or "")
    if mode:
        session["mode"] = mode
    return chat_id, session


def _persist_sessions_with_party(*, force: bool = False) -> bool:
    regular_changed = bool(_original_persist_sessions(force=force))
    try:
        party_changed = persist_party_modes(force=force)
    except Exception:
        logging.exception("[croc-party] failed to persist party state")
        party_changed = False
    return regular_changed or party_changed


def _configure_runtime_with_party_restore() -> None:
    _original_configure_runtime()
    restore_party_modes()


async def _start_duel_vote_with_deadline(chat_id: str, duel: dict) -> None:
    was_voting = duel.get("phase") == "voting"
    await _original_start_duel_vote(chat_id, duel)
    if not was_voting and duel.get("phase") == "voting":
        duel["vote_deadline"] = time.time() + crocodile_modes.DUEL_VOTE_SECONDS


def configure_crocodile_party_state() -> None:
    """Extend the existing Crocodile persistence loop with party-mode state."""
    global _configured
    global _original_persist_sessions, _original_configure_runtime
    global _original_session_to_record, _original_session_from_record
    global _original_start_duel_vote
    if _configured:
        return

    _original_persist_sessions = persistence.persist_crocodile_sessions
    _original_configure_runtime = persistence.configure_crocodile_runtime
    _original_session_to_record = persistence._session_to_record
    _original_session_from_record = persistence._session_from_record
    _original_start_duel_vote = crocodile_modes._start_duel_vote

    persistence._session_to_record = _session_to_record_with_duo
    persistence._session_from_record = _session_from_record_with_duo
    persistence.persist_crocodile_sessions = _persist_sessions_with_party
    persistence.configure_crocodile_runtime = _configure_runtime_with_party_restore
    crocodile_modes._start_duel_vote = _start_duel_vote_with_deadline
    _configured = True
