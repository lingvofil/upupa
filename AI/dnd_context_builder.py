"""Bounded authoritative prompt context for DnD Memory v2."""
from __future__ import annotations

import json
import re


CONTEXT_MAX_CHARS = 6_000
STATE_MAX_CHARS = 2_500
NPC_MAX_CHARS = 500
EVENTS_MAX_CHARS = 700
SCENES_MAX_CHARS = 600
LEGACY_CONTEXT_MAX_CHARS = 300
RECENT_EVENT_LIMIT = 12
RECENT_SCENE_LIMIT = 3
NPC_LIMIT = 6

_HEADER = (
    "ПАМЯТЬ DND V2 — АВТОРИТЕТНЫЙ СНИМОК. "
    "Структурированные факты ниже важнее старого художественного текста. "
    "Не возвращай потраченные предметы, не меняй сохранённую позицию без события, "
    "не оживляй погибших и не отменяй подтверждённые последствия только потому, "
    "что старая реплика в истории говорит иначе."
)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clean(value, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(0, int(limit))]


def _clip(text: str, budget: int, *, marker: str = "\n[…сокращено…]\n") -> str:
    value = str(text or "")
    budget = max(0, int(budget))
    if len(value) <= budget:
        return value
    if budget <= len(marker) + 2:
        return value[:budget]
    payload = budget - len(marker)
    head = max(1, int(payload * 0.58))
    tail = payload - head
    return value[:head] + marker + value[-tail:]


def _tail_lines(lines: list[str], budget: int) -> str:
    selected = []
    used = 0
    for line in reversed([str(value) for value in lines if str(value)]):
        extra = len(line) + (1 if selected else 0)
        if selected and used + extra > budget:
            break
        if not selected and len(line) > budget:
            selected.append(line[-budget:])
            break
        selected.append(line)
        used += extra
    return "\n".join(reversed(selected))


def _inventory_text(rows) -> str:
    out = []
    for raw in rows or []:
        if isinstance(raw, dict):
            name = _clean(raw.get("name"), 80)
            if not name:
                continue
            try:
                quantity = max(1, int(raw.get("quantity", 1) or 1))
            except (TypeError, ValueError):
                quantity = 1
            label = name if quantity == 1 else f"{name}×{quantity}"
            if str(raw.get("kind") or "").casefold() == "artifact":
                label = "✨" + label
            out.append(label)
        else:
            name = _clean(raw, 80)
            if name:
                out.append(name)
        if len(out) >= 8:
            break
    return ", ".join(out) or "нет"


def _conditions_text(rows) -> str:
    result = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        name = _clean(raw.get("name"), 70)
        effect = _clean(raw.get("effect"), 90)
        if name:
            result.append(name + (f" ({effect})" if effect else ""))
        if len(result) >= 4:
            break
    return ", ".join(result) or "нет"


def _achievement_text(session, player_id: str) -> str:
    values = (getattr(session, "learned_achievements", {}) or {}).get(player_id, [])
    result = []
    for raw in values or []:
        if not isinstance(raw, dict):
            continue
        title = _clean(raw.get("title"), 80)
        if not title:
            continue
        try:
            charges = int(raw.get("charges_remaining", 0) or 0)
        except (TypeError, ValueError):
            charges = 0
        result.append(f"{title} [{charges}/1]")
        if len(result) >= 3:
            break
    return ", ".join(result)


def _player_lines(session) -> list[str]:
    participants = getattr(session, "participants", {}) or {}
    profiles = getattr(session, "character_profiles", {}) or {}
    positions = getattr(session, "player_positions", {}) or {}
    sheets = getattr(session, "character_sheets", {}) or {}
    conditions = getattr(session, "conditions", {}) or {}
    inventories = getattr(session, "inventories", {}) or {}
    reputations = getattr(session, "reputations", {}) or {}

    lines = []
    for key, raw_participant in participants.items():
        if not isinstance(raw_participant, dict):
            continue
        player_id = str(raw_participant.get("user_id", key))
        name = _clean(raw_participant.get("name") or f"ID {player_id}", 80)
        bits = [f"ID {player_id} {name}"]

        profile = profiles.get(player_id)
        if isinstance(profile, dict):
            profile_parts = []
            for field in ("style", "strength", "weakness", "special"):
                value = _clean(profile.get(field), 80)
                if value:
                    profile_parts.append(value)
            if profile_parts:
                bits.append("профиль: " + " / ".join(profile_parts))

        position = positions.get(player_id)
        if isinstance(position, dict):
            location = _clean(position.get("location"), 100)
            detail = _clean(position.get("detail"), 120)
            if location:
                bits.append("позиция: " + location + (f" ({detail})" if detail else ""))

        sheet = sheets.get(player_id)
        if isinstance(sheet, dict):
            status = _clean(sheet.get("status"), 30) or "unknown"
            try:
                hp = int(sheet.get("hp", 0))
                max_hp = int(sheet.get("max_hp", 0))
                bits.append(f"HP {hp}/{max_hp}, {status}")
            except (TypeError, ValueError):
                bits.append(f"статус: {status}")

        cond = _conditions_text(conditions.get(player_id))
        if cond != "нет":
            bits.append("состояния: " + cond)

        bits.append("инвентарь: " + _inventory_text(inventories.get(player_id)))

        rep = reputations.get(player_id)
        if isinstance(rep, list) and rep:
            bits.append("репутация: " + "; ".join(_clean(x, 100) for x in rep[-2:]))

        achievements = _achievement_text(session, player_id)
        if achievements:
            bits.append("достижения: " + achievements)

        lines.append("- " + " | ".join(bits))
    return lines or ["- нет зарегистрированных героев"]


def _enemy_lines(session) -> list[str]:
    lines = []
    for key, raw in (getattr(session, "enemy_combatants", {}) or {}).items():
        if not isinstance(raw, dict):
            continue
        name = _clean(raw.get("name") or key, 90)
        status = _clean(raw.get("status"), 30) or "unknown"
        try:
            hp = int(raw.get("hp", 0))
            max_hp = int(raw.get("max_hp", 0))
            ac = int(raw.get("ac", 0))
            lines.append(f"- {name}: HP {hp}/{max_hp}, КБ {ac}, {status}")
        except (TypeError, ValueError):
            lines.append(f"- {name}: {status}")
        if len(lines) >= 6:
            break
    return lines or ["- нет"]


def _clock_lines(session) -> list[str]:
    lines = []
    for key, raw in (getattr(session, "scene_clocks", {}) or {}).items():
        if not isinstance(raw, dict):
            continue
        name = _clean(raw.get("name") or key, 90)
        value = raw.get("value")
        maximum = raw.get("max")
        kind = _clean(raw.get("kind"), 30)
        full = bool(raw.get("full"))
        cause = _clean(raw.get("full_cause"), 120)
        text = f"- {name}: {value}/{maximum}" + (f" [{kind}]" if kind else "")
        if full:
            text += " ЗАПОЛНЕНО" + (f": {cause}" if cause else "")
        lines.append(text)
        if len(lines) >= 4:
            break
    threat = getattr(session, "threat", {}) or {}
    if isinstance(threat, dict) and threat.get("name"):
        lines.append(
            f"- legacy threat {_clean(threat.get('name'), 80)}: "
            f"{threat.get('level', 0)}/{threat.get('max', 0)}"
        )
    return lines or ["- нет"]


def _resource_lines(session) -> list[str]:
    lines = []

    special = getattr(session, "special_move_charges", {}) or {}
    if isinstance(special, dict) and special:
        values = ", ".join(
            f"ID {key}: {_safe_int(value)}"
            for key, value in list(special.items())[:8]
        )
        lines.append("- особые приёмы (заряды): " + values)

    luck = getattr(session, "luck_tokens", {}) or {}
    if isinstance(luck, dict) and luck:
        values = ", ".join(
            f"ID {key}: {int(value or 0)}"
            for key, value in list(luck.items())[:8]
        )
        lines.append("- жетоны удачи: " + values)

    healing = getattr(session, "healing_charges", None)
    if isinstance(healing, list) and healing:
        rendered = []
        for row in healing[:4]:
            if not isinstance(row, dict):
                continue
            owner = row.get("owner_id")
            state = "потрачена" if row.get("used") else ("утрачена" if row.get("lost") else "доступна")
            rendered.append(f"#{row.get('id', '?')} у ID {owner}: {state}")
        if rendered:
            lines.append("- лечилки: " + "; ".join(rendered))

    world_facts = getattr(session, "achievement_world_facts", None)
    if isinstance(world_facts, list):
        facts = [
            _clean(row.get("text"), 140)
            for row in world_facts[-3:]
            if isinstance(row, dict) and _clean(row.get("text"))
        ]
        if facts:
            lines.append("- активные факты достижений: " + " / ".join(facts))

    return lines or ["- нет"]


def _npc_score(name: str, raw: dict, haystack: str, index: int, candidate_name: str) -> tuple:
    score = 0
    normalized_name = name.casefold()
    if normalized_name and normalized_name in haystack:
        score += 20
    if candidate_name and normalized_name == candidate_name:
        score += 12
    for field in ("obligation", "wants", "unresolved"):
        if _clean(raw.get(field)):
            score += 4
    if _clean(raw.get("event")):
        score += 2
    notes = raw.get("notes") or []
    if isinstance(notes, list) and any(_clean(note).casefold() in haystack for note in notes[-2:] if _clean(note)):
        score += 3
    return score, index


def _npc_lines(session, prompt: str, recent_scenes: list[str]) -> list[str]:
    memory = getattr(session, "npc_memory", {}) or {}
    if not isinstance(memory, dict):
        return ["- нет"]

    inherited = {
        str(value).casefold()
        for value in (getattr(session, "world_inherited_npc_keys", []) or [])
    }
    candidate = getattr(session, "world_callback_candidate", {}) or {}
    candidate_name = _clean(candidate.get("name") if isinstance(candidate, dict) else "").casefold()
    haystack = (str(prompt or "") + "\n" + "\n".join(recent_scenes)).casefold()

    ranked = []
    for index, (key, raw) in enumerate(memory.items()):
        if not isinstance(raw, dict):
            continue
        name = _clean(raw.get("name") or key, 100)
        normalized_key = str(key).casefold()
        if normalized_key in inherited and name.casefold() != candidate_name:
            continue
        ranked.append((_npc_score(name, raw, haystack, index, candidate_name), name, raw))

    ranked.sort(key=lambda row: row[0], reverse=True)
    lines = []
    for _score, name, raw in ranked[:NPC_LIMIT]:
        details = []
        for field, label in (
            ("event", "событие"),
            ("obligation", "обязательство"),
            ("wants", "хочет"),
            ("unresolved", "не закрыто"),
        ):
            value = _clean(raw.get(field), 130)
            if value:
                details.append(f"{label}: {value}")
        notes = raw.get("notes") or []
        if isinstance(notes, list):
            recent_notes = [_clean(note, 120) for note in notes[-2:] if _clean(note)]
            if recent_notes:
                details.append("заметки: " + " / ".join(recent_notes))
        lines.append(f"- {name}" + (": " + "; ".join(details) if details else ""))
    return lines or ["- нет"]


def _event_line(event: dict) -> str:
    event_type = _clean(event.get("type"), 80)
    revision = event.get("revision")
    data = event.get("data") or {}
    if not isinstance(data, dict):
        data = {"value": data}
    compact = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"- r{revision} {event_type}: {_clip(compact, 240, marker='…')}"


def _recent_event_text(session) -> str:
    rows = [
        event
        for event in (getattr(session, "event_journal", []) or [])[-RECENT_EVENT_LIMIT:]
        if isinstance(event, dict)
    ]
    if not rows:
        return "- пока нет событий после старта журналирования"
    return _tail_lines([_event_line(event) for event in rows], EVENTS_MAX_CHARS)


def _recent_scene_values(session) -> list[str]:
    rows = []
    for raw in (getattr(session, "scene_log", []) or [])[-RECENT_SCENE_LIMIT:]:
        value = _clean(raw, 700)
        if value:
            rows.append(value)
    return rows


def _recent_scene_text(rows: list[str]) -> str:
    if not rows:
        return "- пока нет зафиксированных сцен"
    return _tail_lines(
        [f"- {index + 1}: {value}" for index, value in enumerate(rows)],
        SCENES_MAX_CHARS,
    )


def _soft_relationship_text(session) -> str:
    rows = []
    for raw in (getattr(session, "social_relationships", []) or [])[-4:]:
        value = _clean(raw, 180)
        if value:
            rows.append("- " + value)
    return "\n".join(rows) or "- данных мало"


def _legacy_tail(legacy_context: str) -> str:
    value = str(legacy_context or "")
    if not value:
        return ""
    if len(value) <= LEGACY_CONTEXT_MAX_CHARS:
        return value
    marker = "[…ранние legacy-секции опущены; authoritative state выше важнее…]\n"
    budget = max(0, LEGACY_CONTEXT_MAX_CHARS - len(marker))
    return marker + value[-budget:]


def build_memory_context(dnd, campaign, session, prompt: str = "") -> str:
    """Build a bounded state-first context for one main DnD generation."""
    recent_scenes = _recent_scene_values(session)

    identity = []
    campaign_id = _clean(getattr(session, "campaign_id", ""), 48)
    revision = getattr(session, "state_revision", 0)
    if campaign_id:
        identity.append(f"служебно: campaign={campaign_id}, revision={revision}")
    selected_plot = _clean(getattr(session, "selected_plot", ""), 220)
    if selected_plot:
        identity.append("сюжетная рамка: " + selected_plot)

    state_parts = [
        "ГЕРОИ И ИХ ТЕКУЩЕЕ СОСТОЯНИЕ:\n"
        + _clip("\n".join(_player_lines(session)), 1_500),
        "АКТИВНЫЕ ПРОТИВНИКИ:\n"
        + _clip("\n".join(_enemy_lines(session)), 320),
        "ШКАЛЫ И УГРОЗЫ:\n"
        + _clip("\n".join(_clock_lines(session)), 320),
        "РЕСУРСЫ И АКТИВНЫЕ ФАКТЫ:\n"
        + _clip("\n".join(_resource_lines(session)), 260),
    ]
    state_text = _clip("\n\n".join(state_parts), STATE_MAX_CHARS)
    npc_text = _clip(
        "\n".join(_npc_lines(session, prompt, recent_scenes)),
        NPC_MAX_CHARS,
    )
    relationships = _clip(_soft_relationship_text(session), 220)

    legacy = ""
    try:
        legacy = campaign._campaign_context(dnd, session)
    except Exception:
        legacy = ""

    sections = [
        _HEADER,
        "\n".join(identity),
        state_text,
        "РЕЛЕВАНТНЫЕ NPC:\n" + npc_text,
        "СОЦГРАФ — только мягкий контекст, не факт мира:\n" + relationships,
        "ПОСЛЕДНИЕ ПОДТВЕРЖДЁННЫЕ ИЗМЕНЕНИЯ:\n" + _recent_event_text(session),
        "ПОСЛЕДНИЕ ХУДОЖЕСТВЕННЫЕ СЦЕНЫ — только для связности:\n" + _recent_scene_text(recent_scenes),
    ]
    # The legacy tail is only 300 characters; the live initiative queue must
    # survive independently of the many campaign context wrappers.
    from AI.dnd_spotlight import _spotlight_context

    sections.insert(2, _spotlight_context(session))
    legacy_tail = _legacy_tail(legacy)
    if legacy_tail:
        sections.append(
            "ДИНАМИЧЕСКИЕ МЕХАНИЧЕСКИЕ ПОДСКАЗКИ СТАРОГО КОНТЕКСТА "
            "(не могут отменять authoritative state выше):\n"
            + legacy_tail
        )

    return _clip("\n\n".join(section for section in sections if section), CONTEXT_MAX_CHARS)


def install_dnd_context_builder(dnd, campaign) -> None:
    if getattr(dnd, "_upupa_dnd_context_builder_installed", False):
        return

    dnd.build_memory_context = lambda session, prompt="": build_memory_context(
        dnd,
        campaign,
        session,
        prompt=prompt,
    )
    dnd._upupa_dnd_context_builder_installed = True


__all__ = [
    "CONTEXT_MAX_CHARS",
    "RECENT_EVENT_LIMIT",
    "RECENT_SCENE_LIMIT",
    "build_memory_context",
    "install_dnd_context_builder",
]
