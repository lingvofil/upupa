"""Deterministic SESSION CANON snapshots for Upupa DnD.

SESSION CANON contains only structured facts that the engine has actually
committed. Planned backstory, old narrative text and model-only guesses are not
promoted into this snapshot.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime


CANON_VERSION = 1
MAX_HERO_ITEMS = 8
MAX_NPCS = 10
MAX_REPUTATION = 4
MAX_CONDITIONS = 4


def _clean(value, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(0, int(limit))]


def _safe_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _item_snapshot(raw):
    if isinstance(raw, dict):
        name = _clean(raw.get("name"), 90)
        if not name:
            return None
        quantity = _safe_int(raw.get("quantity"), 1) or 1
        return {
            "name": name,
            "kind": _clean(raw.get("kind") or "item", 24).casefold() or "item",
            "quantity": max(1, quantity),
        }
    name = _clean(raw, 90)
    if not name:
        return None
    return {"name": name, "kind": "item", "quantity": 1}


def _condition_snapshot(raw):
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get("name"), 80)
    if not name:
        return None
    row = {"name": name}
    effect = _clean(raw.get("effect"), 100)
    if effect:
        row["effect"] = effect
    return row


def _hero_snapshot(session, key: str, participant: dict | None = None) -> dict:
    participant = participant if isinstance(participant, dict) else {}
    player_id = str(participant.get("user_id", key))
    name = _clean(participant.get("name") or f"ID {player_id}", 80)

    row = {"id": player_id, "name": name}

    sheet = (getattr(session, "character_sheets", {}) or {}).get(player_id)
    if isinstance(sheet, dict):
        hp = _safe_int(sheet.get("hp"))
        max_hp = _safe_int(sheet.get("max_hp"))
        status = _clean(sheet.get("status"), 30)
        if hp is not None:
            row["hp"] = hp
        if max_hp is not None:
            row["max_hp"] = max_hp
        if status:
            row["status"] = status

    position = (getattr(session, "player_positions", {}) or {}).get(player_id)
    if isinstance(position, dict):
        location = _clean(position.get("location"), 100)
        detail = _clean(position.get("detail"), 120)
        if location:
            row["position"] = {
                "location": location,
                "detail": detail,
            }

    inventory = []
    for raw in (getattr(session, "inventories", {}) or {}).get(player_id, []):
        item = _item_snapshot(raw)
        if item:
            inventory.append(item)
        if len(inventory) >= MAX_HERO_ITEMS:
            break
    row["inventory"] = inventory

    reputation = [
        _clean(value, 120)
        for value in (getattr(session, "reputations", {}) or {}).get(player_id, [])[-MAX_REPUTATION:]
        if _clean(value, 120)
    ]
    if reputation:
        row["reputation"] = reputation

    conditions = []
    for raw in (getattr(session, "conditions", {}) or {}).get(player_id, []):
        condition = _condition_snapshot(raw)
        if condition:
            conditions.append(condition)
        if len(conditions) >= MAX_CONDITIONS:
            break
    if conditions:
        row["conditions"] = conditions

    return row


def _npc_snapshot(raw, key=None):
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get("name") or key, 90)
    if not name:
        return None
    row = {"name": name}
    for field, limit in (
        ("event", 180),
        ("attitude", 140),
        ("obligation_kind", 32),
        ("obligation", 180),
        ("wants", 180),
        ("unresolved", 180),
    ):
        value = _clean(raw.get(field), limit)
        if value and not (field == "obligation_kind" and value.upper() == "NONE"):
            row[field] = value
    notes = [
        _clean(value, 140)
        for value in (raw.get("notes") or [])[-2:]
        if _clean(value, 140)
    ]
    if notes:
        row["notes"] = notes
    return row


def _clock_snapshot(raw, key=None):
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get("name") or key, 90)
    if not name:
        return None
    value = _safe_int(raw.get("value"), 0)
    maximum = _safe_int(raw.get("max"), 0)
    row = {
        "name": name,
        "value": value,
        "max": maximum,
        "kind": _clean(raw.get("kind"), 30),
        "full": bool(raw.get("full")),
    }
    cause = _clean(raw.get("full_cause"), 140)
    if cause:
        row["cause"] = cause
    return row


def build_session_canon(session) -> dict:
    """Build one deterministic snapshot from committed structured campaign state."""
    participants = getattr(session, "participants", {}) or {}
    heroes = []
    seen = set()

    for key, participant in participants.items():
        if not isinstance(participant, dict):
            continue
        player_id = str(participant.get("user_id", key))
        heroes.append(_hero_snapshot(session, str(key), participant))
        seen.add(player_id)

    # Compatibility for old/lightweight sessions whose state already contains
    # hero-owned maps but no participant roster.
    owned_maps = (
        getattr(session, "character_sheets", {}) or {},
        getattr(session, "player_positions", {}) or {},
        getattr(session, "inventories", {}) or {},
        getattr(session, "reputations", {}) or {},
    )
    extra_keys = []
    for mapping in owned_maps:
        if isinstance(mapping, dict):
            extra_keys.extend(str(key) for key in mapping)
    for key in dict.fromkeys(extra_keys):
        if key in seen:
            continue
        heroes.append(_hero_snapshot(session, key))
        seen.add(key)

    npcs = []
    for key, raw in (getattr(session, "npc_memory", {}) or {}).items():
        npc = _npc_snapshot(raw, key)
        if npc:
            npcs.append(npc)
    npcs = npcs[-MAX_NPCS:]

    clocks = []
    for key, raw in (getattr(session, "scene_clocks", {}) or {}).items():
        clock = _clock_snapshot(raw, key)
        if clock:
            clocks.append(clock)

    threat = getattr(session, "threat", {}) or {}
    threat_row = None
    if isinstance(threat, dict) and _clean(threat.get("name")):
        threat_row = {
            "name": _clean(threat.get("name"), 90),
            "level": _safe_int(threat.get("level"), 0),
            "max": _safe_int(threat.get("max"), 0),
        }

    canon = {
        "version": CANON_VERSION,
        "campaign_id": _clean(getattr(session, "campaign_id", ""), 64) or None,
        "revision": _safe_int(getattr(session, "state_revision", 0), 0),
        "plot": _clean(getattr(session, "selected_plot", ""), 220) or None,
        "heroes": heroes,
        "npcs": npcs,
        "clocks": clocks,
        "threat": threat_row,
    }
    return canon


def _legacy_hero_snapshot(row: dict, chat: dict, key: str) -> dict:
    players = chat.get("players") or {}
    player = players.get(str(key)) or {}
    participants = row.get("participants") or {}
    name = participants.get(str(key)) or player.get("name") or f"ID {key}"
    if isinstance(name, dict):
        name = name.get("name")
    hero = {
        "id": str(key),
        "name": _clean(name, 80),
        "inventory": [],
    }
    for raw in (row.get("inventories") or {}).get(str(key), []):
        item = _item_snapshot(raw)
        if item:
            hero["inventory"].append(item)
        if len(hero["inventory"]) >= MAX_HERO_ITEMS:
            break
    reputation = [
        _clean(value, 120)
        for value in (row.get("reputations") or {}).get(str(key), [])[-MAX_REPUTATION:]
        if _clean(value, 120)
    ]
    if reputation:
        hero["reputation"] = reputation
    if _safe_int(key) in {
        _safe_int(value)
        for value in (row.get("dead_user_ids") or [])
    }:
        hero["status"] = "dead"
        hero["hp"] = 0
    return hero


def build_legacy_archive_canon(row: dict, chat: dict) -> dict:
    """Best-effort structured view for campaigns archived before SESSION CANON."""
    keys = []
    for mapping_name in ("participants", "profiles", "inventories", "reputations"):
        mapping = row.get(mapping_name) or {}
        if isinstance(mapping, dict):
            keys.extend(str(key) for key in mapping)
    heroes = [
        _legacy_hero_snapshot(row, chat, key)
        for key in dict.fromkeys(keys)
    ]

    npcs = []
    for key, raw in (row.get("npc_memory") or {}).items():
        npc = _npc_snapshot(raw, key)
        if npc:
            npcs.append(npc)

    threat = row.get("threat") or {}
    threat_row = None
    if isinstance(threat, dict) and _clean(threat.get("name")):
        threat_row = {
            "name": _clean(threat.get("name"), 90),
            "level": _safe_int(threat.get("level"), 0),
            "max": _safe_int(threat.get("max"), 0),
        }

    raw_clocks = row.get("scene_clocks") or {}
    clocks = []
    if isinstance(raw_clocks, dict):
        for key, raw in raw_clocks.items():
            clock = _clock_snapshot(raw, key)
            if clock:
                clocks.append(clock)
    elif isinstance(raw_clocks, list):
        for raw in raw_clocks:
            clock = _clock_snapshot(raw)
            if clock:
                clocks.append(clock)

    return {
        "version": 0,
        "legacy": True,
        "campaign_id": row.get("campaign_id"),
        "revision": row.get("state_revision"),
        "plot": _clean(row.get("selected_plot"), 220) or None,
        "heroes": heroes,
        "npcs": npcs[-MAX_NPCS:],
        "clocks": clocks,
        "threat": threat_row,
    }


def _item_label(item: dict) -> str:
    name = _clean(item.get("name"), 90)
    quantity = max(1, _safe_int(item.get("quantity"), 1) or 1)
    prefix = "✨" if str(item.get("kind") or "").casefold() == "artifact" else ""
    return prefix + name + (f"×{quantity}" if quantity > 1 else "")


def _hero_line(hero: dict) -> str:
    bits = []
    status = _clean(hero.get("status"), 30)
    hp = _safe_int(hero.get("hp"))
    max_hp = _safe_int(hero.get("max_hp"))
    if hp is not None and max_hp is not None:
        bits.append(f"HP {hp}/{max_hp}" + (f", {status}" if status else ""))
    elif status:
        bits.append(status)

    position = hero.get("position") or {}
    if isinstance(position, dict) and _clean(position.get("location")):
        location = _clean(position.get("location"), 100)
        detail = _clean(position.get("detail"), 100)
        bits.append("позиция: " + location + (f" ({detail})" if detail else ""))

    inventory = [
        _item_label(item)
        for item in (hero.get("inventory") or [])
        if isinstance(item, dict) and _clean(item.get("name"))
    ]
    if inventory:
        bits.append("вещи: " + ", ".join(inventory[:MAX_HERO_ITEMS]))

    reputation = [
        _clean(value, 100)
        for value in (hero.get("reputation") or [])[-MAX_REPUTATION:]
        if _clean(value, 100)
    ]
    if reputation:
        bits.append("репутация: " + "; ".join(reputation))

    name = _clean(hero.get("name") or hero.get("id") or "Герой", 80)
    return "• " + name + ((" — " + " | ".join(bits)) if bits else "")


def _npc_line(npc: dict) -> str:
    details = []
    if _clean(npc.get("event")):
        details.append(_clean(npc.get("event"), 140))
    if _clean(npc.get("obligation")):
        details.append("обязательство: " + _clean(npc.get("obligation"), 140))
    if _clean(npc.get("unresolved")):
        details.append("не закрыто: " + _clean(npc.get("unresolved"), 140))
    if _clean(npc.get("wants")):
        details.append("хочет: " + _clean(npc.get("wants"), 120))
    return "• " + _clean(npc.get("name") or "NPC", 80) + (
        " — " + "; ".join(details) if details else ""
    )


def _clock_line(clock: dict) -> str:
    name = _clean(clock.get("name") or "Шкала", 80)
    value = _safe_int(clock.get("value"), 0)
    maximum = _safe_int(clock.get("max"), 0)
    text = f"• {name}: {value}/{maximum}"
    if clock.get("full"):
        text += " — ЗАПОЛНЕНО"
        if _clean(clock.get("cause")):
            text += ": " + _clean(clock.get("cause"), 120)
    return text


def render_canon_snapshot(canon: dict, *, active: bool, completed_at=None) -> str:
    lines = ["📜 SESSION CANON"]
    if active:
        lines.append("Текущая игра · только структурированно подтверждённые факты.")
    else:
        suffix = ""
        raw_date = _clean(completed_at, 64)
        if raw_date:
            try:
                suffix = " · " + datetime.fromisoformat(raw_date.replace("Z", "+00:00")).strftime("%d.%m.%Y")
            except ValueError:
                suffix = " · " + raw_date.split("T", 1)[0]
        lines.append("Последняя завершённая игра" + suffix + ".")

    if canon.get("legacy"):
        lines.append("Архив старого формата: показаны только поля, которые реально сохранились.")

    if _clean(canon.get("plot")):
        lines.extend(["", "🎬 Приключение", _clean(canon.get("plot"), 220)])

    heroes = [row for row in (canon.get("heroes") or []) if isinstance(row, dict)]
    if heroes:
        lines.extend(["", "👥 Герои"])
        lines.extend(_hero_line(row) for row in heroes)

    npcs = [row for row in (canon.get("npcs") or []) if isinstance(row, dict)]
    if npcs:
        lines.extend(["", "🤝 NPC и незакрытые хвосты"])
        lines.extend(_npc_line(row) for row in npcs[-MAX_NPCS:])

    clocks = [row for row in (canon.get("clocks") or []) if isinstance(row, dict)]
    threat = canon.get("threat")
    if clocks or isinstance(threat, dict):
        lines.extend(["", "⚠️ Угрозы"])
        lines.extend(_clock_line(row) for row in clocks)
        if isinstance(threat, dict) and _clean(threat.get("name")):
            lines.append(
                f"• {_clean(threat.get('name'), 80)}: "
                f"{_safe_int(threat.get('level'), 0)}/{_safe_int(threat.get('max'), 0)}"
            )

    if len(lines) <= 2:
        lines.extend(["", "Пока структурированных игровых фактов нет."])
    return "\n".join(lines)


def render_session_canon(dnd, chat_id: int) -> str:
    from AI import dnd_campaign as campaign

    session = dnd.dnd_sessions.get(int(chat_id))
    if session is not None:
        return render_canon_snapshot(
            build_session_canon(session),
            active=True,
        )

    campaign._load_archive(dnd)
    chat = campaign._chat_history(chat_id)
    rows = list(chat.get("campaigns") or [])
    if not rows:
        return "📜 SESSION CANON пока отсутствует: в этом чате ещё нет сыгранной или завершённой партии."

    latest = rows[-1]
    saved = latest.get("session_canon")
    canon = copy.deepcopy(saved) if isinstance(saved, dict) else build_legacy_archive_canon(latest, chat)
    return render_canon_snapshot(
        canon,
        active=False,
        completed_at=latest.get("completed_at"),
    )


def install_dnd_session_canon_archive(dnd) -> None:
    """Persist one structured SESSION CANON snapshot with every completed game."""
    from AI import dnd_campaign as campaign

    if getattr(campaign, "_upupa_dnd_session_canon_installed", False):
        return

    original_archive = campaign._archive_campaign

    def archive_campaign(dnd_module, session, finale, epilogue):
        result = original_archive(dnd_module, session, finale, epilogue)
        chat = campaign._chat_history(session.chat_id, True)
        rows = chat.get("campaigns") or []
        if rows:
            rows[-1]["session_canon"] = build_session_canon(session)
            campaign._save_archive(dnd_module)
        return result

    campaign._archive_campaign = archive_campaign
    campaign._upupa_dnd_session_canon_installed = True


__all__ = [
    "CANON_VERSION",
    "build_legacy_archive_canon",
    "build_session_canon",
    "install_dnd_session_canon_archive",
    "render_canon_snapshot",
    "render_session_canon",
]
