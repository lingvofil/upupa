"""Bounded canonical event journal for DnD Memory v2.

The journal is derived from changes between durable snapshots. It is diagnostic
and replay-oriented: canonical structured state remains the source of truth.
"""
from __future__ import annotations

import copy
import time
import uuid


EVENT_JOURNAL_LIMIT = 200
_BASELINE_ATTR = "_upupa_dnd_event_baseline"


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ensure(session) -> None:
    campaign_id = str(getattr(session, "campaign_id", "") or "").strip()
    if not campaign_id:
        session.campaign_id = uuid.uuid4().hex
    try:
        session.state_revision = max(0, int(getattr(session, "state_revision", 0) or 0))
    except (TypeError, ValueError):
        session.state_revision = 0
    journal = getattr(session, "event_journal", None)
    if not isinstance(journal, list):
        journal = []
    session.event_journal = [
        copy.deepcopy(item)
        for item in journal[-EVENT_JOURNAL_LIMIT:]
        if isinstance(item, dict)
    ]


def _item_quantity(item) -> int:
    if not isinstance(item, dict):
        return 1
    return max(1, _safe_int(item.get("quantity"), 1))


def _item_name(item) -> str:
    if isinstance(item, dict):
        return " ".join(str(item.get("name") or "").split()).strip()
    return " ".join(str(item or "").split()).strip()


def _item_kind(item) -> str:
    if isinstance(item, dict):
        return str(item.get("kind") or "item").casefold()
    return "item"


def _inventory_snapshot(session) -> dict:
    result = {}
    for owner, rows in (getattr(session, "inventories", {}) or {}).items():
        if not isinstance(rows, list):
            continue
        bucket = {}
        for item in rows:
            name = _item_name(item)
            if not name:
                continue
            kind = _item_kind(item)
            key = f"{kind}:{name.casefold()}"
            row = bucket.setdefault(
                key,
                {"name": name, "kind": kind, "quantity": 0},
            )
            row["quantity"] += _item_quantity(item)
        if bucket:
            result[str(owner)] = bucket
    return result


def _position_snapshot(session) -> dict:
    result = {}
    for owner, row in (getattr(session, "player_positions", {}) or {}).items():
        if not isinstance(row, dict):
            continue
        location = " ".join(str(row.get("location") or "").split()).strip()
        detail = " ".join(str(row.get("detail") or "").split()).strip()
        if location:
            result[str(owner)] = {
                "location": location[:160],
                "detail": detail[:220],
            }
    return result


def _character_snapshot(session) -> dict:
    result = {}
    for owner, row in (getattr(session, "character_sheets", {}) or {}).items():
        if not isinstance(row, dict):
            continue
        result[str(owner)] = {
            "hp": _safe_int(row.get("hp"), 0),
            "max_hp": _safe_int(row.get("max_hp"), 0),
            "status": str(row.get("status") or "").casefold(),
        }
    return result


def _enemy_snapshot(session) -> dict:
    result = {}
    for key, row in (getattr(session, "enemy_combatants", {}) or {}).items():
        if not isinstance(row, dict):
            continue
        result[str(key)] = {
            "name": str(row.get("name") or key)[:100],
            "hp": _safe_int(row.get("hp"), 0),
            "max_hp": _safe_int(row.get("max_hp"), 0),
            "status": str(row.get("status") or "").casefold(),
        }
    return result


def _npc_snapshot(session) -> dict:
    result = {}
    for key, row in (getattr(session, "npc_memory", {}) or {}).items():
        if not isinstance(row, dict):
            continue
        notes = [str(item)[:220] for item in (row.get("notes") or []) if str(item).strip()]
        result[str(key)] = {
            "name": str(row.get("name") or key)[:100],
            "event": str(row.get("event") or "")[:220],
            "latest_note": notes[-1] if notes else "",
            "note_count": len(notes),
        }
    return result


def _conditions_snapshot(session) -> dict:
    result = {}
    for owner, rows in (getattr(session, "conditions", {}) or {}).items():
        if not isinstance(rows, list):
            continue
        compact = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            compact.append(
                {
                    "name": str(row.get("name") or "")[:100],
                    "effect": str(row.get("effect") or "")[:80],
                    "scenes_remaining": row.get("scenes_remaining"),
                    "uses_remaining": row.get("uses_remaining"),
                }
            )
        if compact:
            result[str(owner)] = compact
    return result


def _reputation_snapshot(session) -> dict:
    return {
        str(owner): [str(item)[:220] for item in rows if str(item).strip()]
        for owner, rows in (getattr(session, "reputations", {}) or {}).items()
        if isinstance(rows, list) and rows
    }


def _threat_snapshot(session) -> dict:
    row = getattr(session, "threat", {}) or {}
    if not isinstance(row, dict):
        return {}
    return {
        "name": str(row.get("name") or "")[:100],
        "level": _safe_int(row.get("level"), 0),
        "max": _safe_int(row.get("max"), 0),
    }


def _clock_snapshot(session) -> dict:
    result = {}
    for key, row in (getattr(session, "scene_clocks", {}) or {}).items():
        if isinstance(row, dict):
            result[str(key)] = {
                "name": str(row.get("name") or key)[:100],
                "value": row.get("value"),
                "max": row.get("max"),
                "status": row.get("status"),
            }
    return result


def snapshot_canonical_state(session) -> dict:
    """Return the bounded fields whose changes are useful for replay diagnostics."""
    return {
        "positions": _position_snapshot(session),
        "inventories": _inventory_snapshot(session),
        "characters": _character_snapshot(session),
        "enemies": _enemy_snapshot(session),
        "npcs": _npc_snapshot(session),
        "conditions": _conditions_snapshot(session),
        "reputations": _reputation_snapshot(session),
        "threat": _threat_snapshot(session),
        "clocks": _clock_snapshot(session),
    }


def _event(event_type: str, **data) -> dict:
    return {"type": str(event_type), "data": data}


def _position_events(before: dict, after: dict) -> list[dict]:
    events = []
    for owner in sorted(set(before) | set(after)):
        old = before.get(owner)
        new = after.get(owner)
        if old == new:
            continue
        if new is None:
            events.append(_event("PLAYER_POSITION_CLEARED", player_id=owner, before=old))
        else:
            events.append(
                _event(
                    "PLAYER_POSITION_CHANGED",
                    player_id=owner,
                    before=old,
                    after=new,
                )
            )
    return events


def _inventory_events(before: dict, after: dict) -> list[dict]:
    deltas = []
    owners = sorted(set(before) | set(after))
    for owner in owners:
        old_rows = before.get(owner, {})
        new_rows = after.get(owner, {})
        for key in sorted(set(old_rows) | set(new_rows)):
            old = old_rows.get(key) or {}
            new = new_rows.get(key) or {}
            old_qty = _safe_int(old.get("quantity"), 0)
            new_qty = _safe_int(new.get("quantity"), 0)
            if old_qty == new_qty:
                continue
            exemplar = new or old
            deltas.append(
                {
                    "owner": owner,
                    "key": key,
                    "name": exemplar.get("name") or key,
                    "kind": exemplar.get("kind") or "item",
                    "delta": new_qty - old_qty,
                }
            )

    events = []
    grouped = {}
    for row in deltas:
        grouped.setdefault(row["key"], []).append(dict(row))

    for rows in grouped.values():
        negatives = [row for row in rows if row["delta"] < 0]
        positives = [row for row in rows if row["delta"] > 0]
        for neg in negatives:
            remaining = -neg["delta"]
            for pos in positives:
                if remaining <= 0 or pos["delta"] <= 0:
                    continue
                moved = min(remaining, pos["delta"])
                if moved <= 0:
                    continue
                events.append(
                    _event(
                        "ITEM_TRANSFERRED",
                        name=neg["name"],
                        kind=neg["kind"],
                        quantity=moved,
                        from_player_id=neg["owner"],
                        to_player_id=pos["owner"],
                    )
                )
                remaining -= moved
                pos["delta"] -= moved
            neg["delta"] = -remaining

        for row in negatives + positives:
            if row["delta"] > 0:
                events.append(
                    _event(
                        "ITEM_ADDED",
                        player_id=row["owner"],
                        name=row["name"],
                        kind=row["kind"],
                        quantity=row["delta"],
                    )
                )
            elif row["delta"] < 0:
                events.append(
                    _event(
                        "ITEM_REMOVED",
                        player_id=row["owner"],
                        name=row["name"],
                        kind=row["kind"],
                        quantity=-row["delta"],
                    )
                )
    return events


def _character_events(before: dict, after: dict) -> list[dict]:
    events = []
    for owner in sorted(set(before) | set(after)):
        old = before.get(owner)
        new = after.get(owner)
        if old is None and new is not None:
            events.append(_event("PLAYER_COMBAT_STATE_CREATED", player_id=owner, after=new))
            continue
        if old is None or new is None or old == new:
            continue
        if old.get("hp") != new.get("hp"):
            events.append(
                _event(
                    "PLAYER_HP_CHANGED",
                    player_id=owner,
                    before=old.get("hp"),
                    after=new.get("hp"),
                    max_hp=new.get("max_hp"),
                )
            )
        if old.get("status") != new.get("status"):
            events.append(
                _event(
                    "PLAYER_STATUS_CHANGED",
                    player_id=owner,
                    before=old.get("status"),
                    after=new.get("status"),
                )
            )
    return events


def _enemy_events(before: dict, after: dict) -> list[dict]:
    events = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old is None and new is not None:
            events.append(_event("ENEMY_REGISTERED", enemy_key=key, after=new))
            continue
        if old is not None and new is None:
            events.append(_event("ENEMY_REMOVED", enemy_key=key, before=old))
            continue
        if old == new:
            continue
        if old.get("hp") != new.get("hp"):
            events.append(
                _event(
                    "ENEMY_HP_CHANGED",
                    enemy_key=key,
                    name=new.get("name"),
                    before=old.get("hp"),
                    after=new.get("hp"),
                    max_hp=new.get("max_hp"),
                )
            )
        if old.get("status") != new.get("status"):
            events.append(
                _event(
                    "ENEMY_STATUS_CHANGED",
                    enemy_key=key,
                    name=new.get("name"),
                    before=old.get("status"),
                    after=new.get("status"),
                )
            )
    return events


def _map_change_events(event_type: str, before: dict, after: dict, id_field: str) -> list[dict]:
    events = []
    for key in sorted(set(before) | set(after)):
        old = before.get(key)
        new = after.get(key)
        if old != new:
            events.append(_event(event_type, **{id_field: key}, before=old, after=new))
    return events


def diff_canonical_state(before: dict, after: dict) -> list[dict]:
    """Translate one durable state delta into compact structured events."""
    events = []
    events.extend(_position_events(before.get("positions", {}), after.get("positions", {})))
    events.extend(_inventory_events(before.get("inventories", {}), after.get("inventories", {})))
    events.extend(_character_events(before.get("characters", {}), after.get("characters", {})))
    events.extend(_enemy_events(before.get("enemies", {}), after.get("enemies", {})))
    events.extend(_map_change_events("NPC_MEMORY_CHANGED", before.get("npcs", {}), after.get("npcs", {}), "npc_key"))
    events.extend(_map_change_events("CONDITIONS_CHANGED", before.get("conditions", {}), after.get("conditions", {}), "player_id"))
    events.extend(_map_change_events("REPUTATION_CHANGED", before.get("reputations", {}), after.get("reputations", {}), "player_id"))
    if before.get("threat") != after.get("threat"):
        events.append(_event("THREAT_CHANGED", before=before.get("threat"), after=after.get("threat")))
    events.extend(_map_change_events("SCENE_CLOCK_CHANGED", before.get("clocks", {}), after.get("clocks", {}), "clock_id"))
    return events


def prepare_session_events(session) -> list[dict]:
    """Commit canonical changes since the previous durable snapshot."""
    _ensure(session)
    current = snapshot_canonical_state(session)
    previous = getattr(session, _BASELINE_ATTR, None)
    if not isinstance(previous, dict):
        setattr(session, _BASELINE_ATTR, copy.deepcopy(current))
        return []

    changes = diff_canonical_state(previous, current)
    setattr(session, _BASELINE_ATTR, copy.deepcopy(current))
    if not changes:
        return []

    session.state_revision += 1
    revision = int(session.state_revision)
    created_at = time.time()
    committed = []
    for sequence, raw in enumerate(changes, start=1):
        item = {
            "event_id": f"{session.campaign_id}:{revision}:{sequence}",
            "campaign_id": session.campaign_id,
            "revision": revision,
            "sequence": sequence,
            "type": raw["type"],
            "data": copy.deepcopy(raw.get("data") or {}),
            "created_at": created_at,
        }
        committed.append(item)

    session.event_journal = (session.event_journal + committed)[-EVENT_JOURNAL_LIMIT:]
    return committed


def _restore(session, _data) -> None:
    _ensure(session)
    setattr(session, _BASELINE_ATTR, snapshot_canonical_state(session))


def current_identity(session) -> tuple[str | None, int]:
    campaign_id = str(getattr(session, "campaign_id", "") or "").strip() or None
    revision = max(0, _safe_int(getattr(session, "state_revision", 0), 0))
    return campaign_id, revision


def install_dnd_event_journal(dnd, *, state_policy) -> None:
    """Register identity, revision, journal persistence and pre-persist diffing."""
    if getattr(dnd, "_upupa_dnd_event_journal_installed", False):
        return

    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field("campaign_id", lambda session: (_ensure(session), session.campaign_id)[1])
    state_policy.add_state_field("state_revision", lambda session: (_ensure(session), session.state_revision)[1])
    state_policy.add_state_field(
        "event_journal",
        lambda session: (_ensure(session), copy.deepcopy(session.event_journal))[1],
    )
    # Installed after the other state modules so this baseline sees their fully
    # normalized restored representation.
    state_policy.add_restore_hook(_restore)
    dnd.register_persist_hook(prepare_session_events)

    # Existing in-memory sessions get an identity and a no-op baseline instead
    # of producing a synthetic history for changes that happened before deploy.
    for session in (getattr(dnd, "dnd_sessions", {}) or {}).values():
        _ensure(session)
        setattr(session, _BASELINE_ATTR, snapshot_canonical_state(session))

    dnd._upupa_dnd_event_journal_installed = True


__all__ = [
    "EVENT_JOURNAL_LIMIT",
    "current_identity",
    "diff_canonical_state",
    "install_dnd_event_journal",
    "prepare_session_events",
    "snapshot_canonical_state",
]
