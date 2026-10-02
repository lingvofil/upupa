"""Persisted interaction windows, independent of narrator message counts.

Integration: register ``ensure``, ``restore`` and the ``dnd_lifecycle_v1`` state
field with DndCampaignStatePolicy. Call sync_window after changing the actual
INPUT/POLL/ROLL state. A roll is a phase of the same interaction, not a new turn.
Read-only menus never call begin_window or tick_event.
"""
from __future__ import annotations

import copy
import hashlib
import uuid


STATE_FIELD = "dnd_lifecycle_v1"
VERSION = 1
KINDS = {"personal", "group", "poll", "reaction"}
WAITING_PHASES = {"WAITING_ACTION": "input", "WAITING_ROLL": "roll", "WAITING_POLL": "poll"}


def _integer(value, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _actors(values):
    result = []
    for value in values or []:
        try:
            uid = int(value)
        except (TypeError, ValueError):
            continue
        if uid not in result:
            result.append(uid)
    return result


def ensure(session) -> dict:
    """Normalize only lifecycle fields; legacy durations keep narration ticks."""
    raw = getattr(session, STATE_FIELD, None)
    row = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    row["version"] = VERSION
    row.setdefault("scene_id", "scene:" + uuid.uuid4().hex[:16])
    row["narration_seq"] = max(_integer(row.get("narration_seq")), _integer(getattr(session, "scene_count", 0)))
    # SCENES in old saves counts generated narrative. Never reinterpret it as
    # combat rounds merely because the new narrator runs less frequently.
    row.setdefault("legacy_condition_policy", "narration")
    row["window_seq"] = _integer(row.get("window_seq"))
    row["round_id"] = row.get("round_id") or None
    row["round_number"] = _integer(row.get("round_number"))
    row["round_acted"] = _actors(row.get("round_acted"))
    row["tick_counts"] = {str(k): _integer(v) for k, v in (row.get("tick_counts") or {}).items()} if isinstance(row.get("tick_counts"), dict) else {}
    row["tick_ids"] = [str(v) for v in (row.get("tick_ids") or [])][-200:]
    window = row.get("window")
    if isinstance(window, dict) and window.get("turn_id"):
        window["kind"] = window.get("kind") if window.get("kind") in KINDS else "group"
        window["actors"] = _actors(window.get("actors"))
        window["phase"] = str(window.get("phase") or "input")
        window["status"] = "closed" if window.get("status") == "closed" else "open"
    else:
        row["window"] = None
    setattr(session, STATE_FIELD, row)
    return row


def restore(session, data) -> None:
    source = data if isinstance(data, dict) else {}
    setattr(session, STATE_FIELD, copy.deepcopy(source.get(STATE_FIELD) or {}))
    ensure(session)


def state_field(session) -> dict:
    return copy.deepcopy(ensure(session))


def current_window(session) -> dict | None:
    window = ensure(session).get("window")
    return copy.deepcopy(window) if isinstance(window, dict) else None


def current_turn_id(session) -> str | None:
    return (current_window(session) or {}).get("turn_id")


def begin_window(session, *, kind="group", actors=(), phase="input", source_id=None) -> dict:
    if kind not in KINDS or phase not in {"input", "roll", "poll", "reaction"}:
        raise ValueError("Unknown DnD interaction kind/phase")
    row = ensure(session)
    old = row.get("window")
    if isinstance(old, dict) and old.get("status") == "open":
        if source_id is not None and str(old.get("source_id")) == str(source_id) and old.get("phase") == phase:
            return copy.deepcopy(old)
        old["status"] = "closed"
    row["window_seq"] += 1
    campaign = str(getattr(session, "campaign_id", "") or "legacy")
    window = {"turn_id": f"{campaign}:w{row['window_seq']}", "kind": kind, "phase": phase,
              "actors": _actors(actors), "status": "open", "source_id": str(source_id) if source_id is not None else None,
              "scene_id": row["scene_id"], "round_id": row["round_id"], "action_ids": {}}
    row["window"] = window
    return copy.deepcopy(window)


def close_window(session, *, turn_id=None, reason="resolved") -> bool:
    row = ensure(session)
    window = row.get("window")
    if not isinstance(window, dict) or window.get("status") != "open":
        return False
    if turn_id is not None and window["turn_id"] != turn_id:
        return False
    window.update(status="closed", close_reason=str(reason))
    return True


def sync_window(session) -> dict | None:
    """Follow persisted prompts; INPUT -> ROLL preserves logical turn identity."""
    row = ensure(session)
    state = str(getattr(session, "state", "") or "")
    phase = WAITING_PHASES.get(state)
    window = row.get("window")
    if phase is None:
        if state in {"ENDING", "FINISHED", "LOBBY", "WAITING_PLOT", "WAITING_MODE", "WAITING_BACKSTORY"}:
            close_window(session, reason=state.lower())
        return current_window(session)
    if phase == "roll":
        pending = getattr(session, "pending_roll", None) or {}
        actors = _actors(pending.get("target_user_ids"))
        if isinstance(window, dict) and window.get("status") == "open":
            window["phase"] = "roll"
            # Keep the parent group's actors; the addressed roll has its own
            # authorization in pending_roll. Other actions still belong to it.
            window["roll_actors"] = actors
            return copy.deepcopy(window)
        return begin_window(session, kind="personal" if len(actors) == 1 else "group", actors=actors, phase="roll", source_id=pending.get("action_id"))
    if phase == "poll":
        pending = getattr(session, "pending_poll", None) or {}
        actors = _actors(pending.get("target_user_ids"))
        source = getattr(session, "current_poll_id", None) or pending.get("message_id")
        kind = "poll"
    else:
        actors = _actors(getattr(session, "action_target_user_ids", ()))
        source = getattr(session, "action_prompt_message_id", None)
        kind = "personal" if len(actors) == 1 else "group"
    if not actors:
        actors = active_actor_ids(session)
    if isinstance(window, dict) and window.get("status") == "open" and window.get("phase") == phase:
        if source is None or window.get("source_id") == str(source):
            if not window.get("resolved_actors"):
                window.update(actors=actors, kind=kind)
            if source is not None:
                window["source_id"] = str(source)
            return copy.deepcopy(window)
        # open_action_window persists once before Telegram returns its message.
        if window.get("source_id") is None:
            window.update(source_id=str(source), actors=actors, kind=kind)
            return copy.deepcopy(window)
    return begin_window(session, kind=kind, actors=actors, phase=phase, source_id=source)


def active_actor_ids(session) -> list[int]:
    result = []
    for key, player in (getattr(session, "participants", {}) or {}).items():
        if not isinstance(player, dict) or not player.get("active", True):
            continue
        sheet = (getattr(session, "character_sheets", {}) or {}).get(str(key)) or {}
        if sheet.get("status") == "dead" or ("hp" in sheet and _integer(sheet["hp"]) <= 0):
            continue
        try:
            result.append(int(player.get("user_id", key)))
        except (TypeError, ValueError):
            pass
    return sorted(set(result))


def action_id(session, actor_id: int) -> str:
    row = ensure(session)
    window = row.get("window")
    if not isinstance(window, dict) or window.get("status") != "open":
        raise ValueError("No open interaction window")
    key = str(int(actor_id))
    ids = window.setdefault("action_ids", {})
    ids.setdefault(key, hashlib.sha256(f"{window['turn_id']}:{key}".encode()).hexdigest()[:24])
    return ids[key]


def new_scene(session, *, scene_id=None) -> str:
    row = ensure(session)
    close_window(session, reason="scene-ended")
    row = ensure(session)
    row.update(scene_id=str(scene_id or "scene:" + uuid.uuid4().hex[:16]), round_id=None, round_number=0, round_acted=[])
    return row["scene_id"]


def start_combat_round(session) -> str:
    row = ensure(session)
    row["round_number"] += 1
    row["round_id"] = f"{row['scene_id']}:r{row['round_number']}"
    row["round_acted"] = []
    return row["round_id"]


def note_actor_resolved(session, actor_id: int) -> bool:
    """Return round completion only when an explicit combat round exists."""
    row = ensure(session)
    if not row["round_id"]:
        return False
    if int(actor_id) not in row["round_acted"]:
        row["round_acted"].append(int(actor_id))
    return set(active_actor_ids(session)).issubset(row["round_acted"])


def tick_event(session, kind: str, event_id: str, *, actor_id=None) -> bool:
    """Explicit versioned effect clocks. Replaying an event never ticks twice."""
    if kind not in {"action", "window", "round", "scene", "narration"} or not event_id:
        raise ValueError("Explicit tick kind and event ID required")
    row = ensure(session)
    key = f"{kind}:{event_id}"
    if key in row["tick_ids"]:
        return False
    row["tick_ids"] = (row["tick_ids"] + [key])[-200:]
    row["tick_counts"][kind] = row["tick_counts"].get(kind, 0) + 1
    for actor, effects in (getattr(session, "conditions", {}) or {}).items():
        kept = []
        for effect in effects:
            expiry = effect.get("expiry") if isinstance(effect, dict) else None
            if isinstance(expiry, dict) and expiry.get("version") == 1 and expiry.get("tick") == kind:
                if expiry.get("actor_id") is not None and str(expiry["actor_id"]) != str(actor_id):
                    kept.append(effect)
                    continue
                expiry["remaining"] = max(0, _integer(expiry.get("remaining")) - 1)
                if not expiry["remaining"]:
                    continue
            kept.append(effect)
        session.conditions[actor] = kept
    return True
