"""Generic two-slot scene clocks for participant-mode Upupa DnD."""
from __future__ import annotations

import re


SCENE_CLOCKS_MARKER = "СЦЕНОВЫЕ ШКАЛЫ DND УПУПЫ"
SCENE_CLOCKS_RULES = f"""
{SCENE_CLOCKS_MARKER}.
Когда сцене полезно явное накопление прогресса или давления, используй до ДВУХ независимых шкал.
Они не обязаны быть противоположными и могут расти одновременно: успех с ценой может дать прогресс +2 и тревогу +1.
Создание/обновление шкалы:
[CLOCK:SET;ID:access;NAME:Доступ к хранилищу;KIND:PROGRESS;VALUE:0;MAX:6;WHEN_FULL:хранилище открыто]
[CLOCK:SET;ID:alarm;NAME:Тревога;KIND:DANGER;VALUE:0;MAX:6;WHEN_FULL:прибывает стража]
Изменение:
[CLOCK:DELTA;ID:access;DELTA:2;CAUSE:вскрыли внешний замок]
[CLOCK:DELTA;ID:alarm;DELTA:1;CAUSE:подняли шум]
Умное решение может честно обойти шкалу:
[CLOCK:COMPLETE;ID:access;CAUSE:нашли настоящий ключ]
Если шкала больше не относится к текущей сцене:
[CLOCK:CLEAR;ID:access]
KIND: PROGRESS, DANGER или NEUTRAL. MAX обычно 4 или 6, допустимо 2..8.
WHEN_FULL — конкретное изменение ситуации, а не абстрактные «победа»/«поражение».
Заполнение шкалы устанавливает это событие как факт мира, но не обязано заканчивать приключение.
Не заставляй игроков набивать деления, если уже найдено прямое решение проблемы.
Не создавай шкалы для каждой мелочи и не используй реальное время, минуты или количество сообщений.
Старый THREAT оставлен только для совместимости старых партий; для новых сцен предпочитай CLOCK.
""".strip()

_CLOCK_RE = re.compile(r"\[CLOCK:([^\]]*)\]", re.I)
_VALID_KINDS = {"PROGRESS", "DANGER", "NEUTRAL"}
_MAX_ACTIVE = 2


def _clean(value, limit=220) -> str:
    return " ".join(str(value or "").replace("[", "").replace("]", "").split()).strip(" ;")[:limit]


def _clock_id(value) -> str:
    text = _clean(value, 80).casefold().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9_-]+", "-", text, flags=re.I).strip("-_")[:64]


def _parse(raw: str) -> tuple[str, dict[str, str]]:
    parts = [part.strip() for part in str(raw or "").split(";") if part.strip()]
    head = parts[0].upper() if parts else ""
    fields = {}
    for part in parts[1:]:
        key, sep, value = part.partition(":")
        if sep and value.strip():
            fields[key.strip().upper()] = value.strip()
    return head, fields


def _bounded_int(value, default: int, low: int, high: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _ensure(session) -> None:
    raw = getattr(session, "scene_clocks", None)
    if not isinstance(raw, dict):
        raw = {}
    cleaned = {}
    for key, item in raw.items():
        if not isinstance(item, dict):
            continue
        clock_id = _clock_id(item.get("id") or key)
        if not clock_id:
            continue
        maximum = _bounded_int(item.get("max"), 6, 2, 8)
        value = _bounded_int(item.get("value"), 0, 0, maximum)
        kind = str(item.get("kind") or "NEUTRAL").upper()
        if kind not in _VALID_KINDS:
            kind = "NEUTRAL"
        cleaned[clock_id] = {
            "id": clock_id,
            "name": _clean(item.get("name") or clock_id, 100),
            "kind": kind,
            "value": value,
            "max": maximum,
            "when_full": _clean(item.get("when_full"), 220),
            "full": bool(item.get("full")) or value >= maximum,
            "full_cause": _clean(item.get("full_cause"), 220),
            "history": [dict(row) for row in (item.get("history") or [])[-12:] if isinstance(row, dict)],
        }
        if len(cleaned) >= _MAX_ACTIVE:
            break
    session.scene_clocks = cleaned


def _restore(session, data) -> None:
    raw = (data or {}).get("scene_clocks") if isinstance(data, dict) else None
    session.scene_clocks = dict(raw) if isinstance(raw, dict) else {}
    _ensure(session)


def _bar(value: int, maximum: int) -> str:
    value = max(0, min(maximum, int(value)))
    return "■" * value + "□" * (maximum - value)


def _icon(kind: str) -> str:
    return {"PROGRESS": "🎯", "DANGER": "🚨", "NEUTRAL": "⏱"}.get(str(kind).upper(), "⏱")


def _set_clock(session, fields: dict[str, str]) -> str | None:
    _ensure(session)
    clock_id = _clock_id(fields.get("ID") or fields.get("NAME"))
    if not clock_id:
        return None
    existing = session.scene_clocks.get(clock_id)
    if existing is None and len(session.scene_clocks) >= _MAX_ACTIVE:
        return None

    maximum = _bounded_int(fields.get("MAX"), int((existing or {}).get("max", 6)), 2, 8)
    value = _bounded_int(fields.get("VALUE"), int((existing or {}).get("value", 0)), 0, maximum)
    kind = str(fields.get("KIND") or (existing or {}).get("kind") or "NEUTRAL").upper()
    if kind not in _VALID_KINDS:
        kind = "NEUTRAL"
    full = value >= maximum
    row = {
        "id": clock_id,
        "name": _clean(fields.get("NAME") or (existing or {}).get("name") or clock_id, 100),
        "kind": kind,
        "value": value,
        "max": maximum,
        "when_full": _clean(fields.get("WHEN_FULL") or (existing or {}).get("when_full"), 220),
        "full": full,
        "full_cause": _clean((existing or {}).get("full_cause"), 220) if full else "",
        "history": list((existing or {}).get("history") or [])[-12:],
    }
    session.scene_clocks[clock_id] = row
    return None


def _delta_clock(session, fields: dict[str, str]) -> str | None:
    _ensure(session)
    clock_id = _clock_id(fields.get("ID"))
    row = session.scene_clocks.get(clock_id)
    if not isinstance(row, dict):
        return None

    try:
        delta = int(str(fields.get("DELTA") or "0").strip())
    except (TypeError, ValueError):
        return None
    delta = max(-4, min(4, delta))
    if not delta:
        return None

    old = int(row.get("value", 0) or 0)
    maximum = int(row.get("max", 6) or 6)
    new = max(0, min(maximum, old + delta))
    actual = new - old
    if not actual:
        return None
    cause = _clean(fields.get("CAUSE") or "ситуация изменилась", 220)
    row["value"] = new
    row.setdefault("history", []).append({
        "delta": actual,
        "cause": cause,
        "scene": int(getattr(session, "scene_count", 0) or 0),
    })
    row["history"] = row["history"][-12:]

    became_full = old < maximum and new >= maximum
    if became_full:
        row["full"] = True
        row["full_cause"] = cause
        event = row.get("when_full") or "шкала заполнена и ситуация меняется"
        return (
            f"{_icon(row.get('kind'))} {row.get('name')}: {_bar(new, maximum)} {new}/{maximum} "
            f"({actual:+d}: {cause}).\n⚡ Событие: {event}."
        )
    if new < maximum:
        row["full"] = False
        row["full_cause"] = ""
    return (
        f"{_icon(row.get('kind'))} {row.get('name')}: {_bar(new, maximum)} {new}/{maximum} "
        f"({actual:+d}: {cause})."
    )


def _complete_clock(session, fields: dict[str, str]) -> str | None:
    _ensure(session)
    clock_id = _clock_id(fields.get("ID"))
    row = session.scene_clocks.get(clock_id)
    if not isinstance(row, dict):
        return None
    maximum = int(row.get("max", 6) or 6)
    old = int(row.get("value", 0) or 0)
    cause = _clean(fields.get("CAUSE") or "найден прямой способ решить задачу", 220)
    row["value"] = maximum
    row["full"] = True
    row["full_cause"] = cause
    row.setdefault("history", []).append({
        "delta": maximum - old,
        "cause": cause,
        "scene": int(getattr(session, "scene_count", 0) or 0),
        "complete": True,
    })
    row["history"] = row["history"][-12:]
    event = row.get("when_full") or "цель шкалы достигнута"
    return (
        f"{_icon(row.get('kind'))} {row.get('name')}: {_bar(maximum, maximum)} {maximum}/{maximum} "
        f"(обход: {cause}).\n⚡ Событие: {event}."
    )


def _clear_clock(session, fields: dict[str, str]) -> None:
    _ensure(session)
    clock_id = _clock_id(fields.get("ID") or fields.get("NAME"))
    if clock_id:
        session.scene_clocks.pop(clock_id, None)


def apply_clock_metadata(session, original_text: str, cleaned: str, notices: list[str]):
    _ensure(session)
    extra = []
    for match in _CLOCK_RE.finditer(str(original_text or "")):
        head, fields = _parse(match.group(1))
        notice = None
        if head == "SET":
            notice = _set_clock(session, fields)
        elif head == "DELTA":
            notice = _delta_clock(session, fields)
        elif head == "COMPLETE":
            notice = _complete_clock(session, fields)
        elif head == "CLEAR":
            _clear_clock(session, fields)
        if notice:
            extra.append(notice)
    return _CLOCK_RE.sub("", str(cleaned or "")).strip(), list(notices or []) + extra


def render_clocks(clocks) -> list[str]:
    if not isinstance(clocks, dict):
        return []
    lines = []
    for row in clocks.values():
        if not isinstance(row, dict):
            continue
        maximum = _bounded_int(row.get("max"), 6, 2, 8)
        value = _bounded_int(row.get("value"), 0, 0, maximum)
        line = (
            f"{_icon(row.get('kind'))} {row.get('name') or row.get('id')}: "
            f"{_bar(value, maximum)} {value}/{maximum}"
        )
        if row.get("full") and row.get("when_full"):
            line += f" — {row['when_full']}"
        lines.append(line)
    return lines[:_MAX_ACTIVE]


def _context(session) -> str:
    _ensure(session)
    lines = render_clocks(session.scene_clocks)
    detail = []
    for row in session.scene_clocks.values():
        if row.get("full") and row.get("when_full"):
            detail.append(
                f"- ЗАПОЛНЕНО {row.get('name')}: событие «{row.get('when_full')}» уже установлено как факт мира; "
                "учитывай его немедленно и не откатывай шкалу ради удобства сюжета."
            )
    if not lines:
        return SCENE_CLOCKS_RULES
    return (
        SCENE_CLOCKS_RULES
        + "\nАКТИВНЫЕ ШКАЛЫ:\n"
        + "\n".join(lines)
        + (("\n" + "\n".join(detail)) if detail else "")
    )


def install_dnd_scene_clocks(dnd, *, state_policy, metadata_policy) -> None:
    from AI import dnd_campaign as campaign
    from AI import dnd_state_commands as state_commands

    if getattr(dnd, "_upupa_dnd_scene_clocks_installed", False):
        return

    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field("scene_clocks", lambda session: dict(getattr(session, "scene_clocks", {}) or {}))
    state_policy.add_restore_hook(_restore)
    metadata_policy.add_postprocessor(apply_clock_metadata)

    if SCENE_CLOCKS_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + SCENE_CLOCKS_RULES

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        return original_context(dnd_module, session) + "\n" + _context(session)

    campaign._campaign_context = campaign_context

    original_archive = campaign._archive_campaign

    def archive_campaign(dnd_module, session, finale, epilogue):
        original_archive(dnd_module, session, finale, epilogue)
        latest = campaign._latest_campaign(session.chat_id)
        if isinstance(latest, dict):
            latest["scene_clocks"] = dict(getattr(session, "scene_clocks", {}) or {})
            campaign._save_archive(dnd_module)

    campaign._archive_campaign = archive_campaign

    original_render_status = state_commands.render_status

    def render_status(dnd_module, chat_id):
        text = original_render_status(dnd_module, chat_id)
        session = dnd_module.dnd_sessions.get(int(chat_id))
        if session is not None:
            clocks = getattr(session, "scene_clocks", {}) or {}
        else:
            latest = campaign._latest_campaign(chat_id)
            clocks = latest.get("scene_clocks") if isinstance(latest, dict) else {}
        lines = render_clocks(clocks)
        if not lines:
            return text
        return text + "\n\n⏱ Шкалы сцены\n" + "\n".join(lines)

    state_commands.render_status = render_status
    dnd._upupa_dnd_scene_clocks_installed = True


__all__ = [
    "SCENE_CLOCKS_MARKER",
    "SCENE_CLOCKS_RULES",
    "apply_clock_metadata",
    "render_clocks",
    "install_dnd_scene_clocks",
]
