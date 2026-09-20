"""Persistent per-player physical positions for participant-mode Upupa DnD."""
from __future__ import annotations

import re


POSITION_MARKER = "ПОЗИЦИИ ГЕРОЕВ DND УПУПЫ"
POSITION_RULES = f"""
{POSITION_MARKER}.
Физическое местоположение героя — устойчивый факт сцены, а не декоративная деталь.
Если персонаж уже находится внутри банки, в лазе, на крыше, в машине, связан у стены и т.п.,
не переноси его в другое место без явного действия или события, которое это объясняет.
Сохранённые ниже позиции считаются авторитетными, пока сцена явно их не изменила.

После сюжетной сцены фиксируй позицию каждого героя, чьё местоположение однозначно известно:
[POSITION:SET;PLAYER:123;LOCATION:банка с пивом;DETAIL:ползёт внутри банки]
PLAYER — только ID участника игры. LOCATION — короткое физическое место.
DETAIL — необязательная конкретизация позы, контейнера, транспорта или привязки к объекту.
Если местоположение действительно стало неизвестно:
[POSITION:CLEAR;PLAYER:123]
Не выдумывай POSITION ради заполнения тега. Не создавай одного героя одновременно в двух местах.
""".strip()

_META_RE = re.compile(r"\[POSITION:([^\]]*)\]", re.I)


def _clean(value, limit=180) -> str:
    return " ".join(str(value or "").replace("[", "").replace("]", "").split()).strip(" ;")[:limit]


def _fields(raw: str) -> tuple[str, dict[str, str]]:
    parts = [part.strip() for part in str(raw or "").split(";") if part.strip()]
    head = parts[0] if parts else ""
    fields = {}
    for part in parts[1:]:
        key, sep, value = part.partition(":")
        if sep and value.strip():
            fields[key.strip().upper()] = value.strip()
    return head, fields


def _participant_ids(session) -> set[str]:
    result = set()
    for key, row in (getattr(session, "participants", {}) or {}).items():
        try:
            result.add(str(int(row.get("user_id", key))))
        except (TypeError, ValueError):
            continue
    return result


def _ensure(session) -> None:
    if not isinstance(getattr(session, "player_positions", None), dict):
        session.player_positions = {}


def _restore(session, data) -> None:
    raw = (data or {}).get("player_positions") if isinstance(data, dict) else None
    session.player_positions = {
        str(player): dict(position)
        for player, position in (raw or {}).items()
        if isinstance(position, dict)
    }
    _ensure(session)


def apply_player_position_metadata(session, original_text: str, cleaned: str, notices: list[str]):
    _ensure(session)
    valid_players = _participant_ids(session)
    for match in _META_RE.finditer(str(original_text or "")):
        head, fields = _fields(match.group(1))
        player = str(fields.get("PLAYER") or "")
        if player not in valid_players:
            continue
        if head.upper() == "CLEAR":
            session.player_positions.pop(player, None)
            continue
        if head.upper() != "SET":
            continue
        location = _clean(fields.get("LOCATION"), 120)
        if not location:
            continue
        session.player_positions[player] = {
            "location": location,
            "detail": _clean(fields.get("DETAIL"), 180),
            "updated_scene": int(getattr(session, "scene_count", 0) or 0),
        }
    return _META_RE.sub("", str(cleaned or "")).strip(), list(notices or [])


def render_player_position_context(session) -> str:
    _ensure(session)
    lines = []
    for player, position in session.player_positions.items():
        if not isinstance(position, dict):
            continue
        participant = (getattr(session, "participants", {}) or {}).get(str(player), {})
        name = participant.get("name") or f"ID {player}"
        location = _clean(position.get("location"), 120)
        if not location:
            continue
        detail = _clean(position.get("detail"), 180)
        suffix = f"; {detail}" if detail else ""
        lines.append(f"- {name} (ID {player}): {location}{suffix}")
    return (
        POSITION_RULES
        + "\nТЕКУЩИЕ ПОЗИЦИИ ГЕРОЕВ:\n"
        + ("\n".join(lines[-12:]) if lines else "- пока не зафиксированы")
    )


def install_dnd_player_positions(dnd, *, state_policy, metadata_policy) -> None:
    from AI import dnd_campaign as campaign

    if getattr(dnd, "_upupa_dnd_player_positions_installed", False):
        return

    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field(
        "player_positions",
        lambda session: {
            str(player): dict(position)
            for player, position in (getattr(session, "player_positions", {}) or {}).items()
            if isinstance(position, dict)
        },
    )
    state_policy.add_restore_hook(_restore)
    metadata_policy.add_postprocessor(apply_player_position_metadata)

    if POSITION_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + POSITION_RULES

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        return original_context(dnd_module, session) + "\n" + render_player_position_context(session)

    campaign._campaign_context = campaign_context
    dnd._upupa_dnd_player_positions_installed = True


__all__ = [
    "POSITION_MARKER",
    "POSITION_RULES",
    "apply_player_position_metadata",
    "install_dnd_player_positions",
    "render_player_position_context",
]
