"""Make the governing hero ability visible before every DnD check/save."""
from __future__ import annotations

import re


ROLL_ABILITY_MARKER = "ВИДИМАЯ ХАРАКТЕРИСТИКА БРОСКА DND УПУПЫ"
ABILITY_LABELS = {
    "STR": "Сила",
    "DEX": "Ловкость",
    "CON": "Телосложение",
    "INT": "Интеллект",
    "WIS": "Мудрость",
    "CHA": "Харизма",
}
_ROLL_RE = re.compile(r"\[ACTION:ROLL;(.*?)\]", re.I | re.S)


def _fields(raw: str) -> dict[str, str]:
    result = {}
    for part in str(raw or "").split(";"):
        key, sep, value = part.partition(":")
        if sep and value.strip():
            result[key.strip().upper()] = value.strip()
    return result


def _infer_check_ability(reason: str) -> str:
    text = str(reason or "").casefold()
    if any(word in text for word in ("поднят", "поднять", "слом", "выбить", "удерж", "толк", "тащ", "перетащ", "борьб")):
        return "STR"
    if any(word in text for word in ("краст", "спрят", "ловк", "прыг", "перепрыг", "баланс", "метн", "уклон", "карман")):
        return "DEX"
    if any(word in text for word in ("вспомн", "знан", "расслед", "разгад", "прочит", "маг", "механизм", "изуч")):
        return "INT"
    if any(word in text for word in ("замет", "услыш", "почувств", "след", "интуиц", "прониц", "осмотр")):
        return "WIS"
    if any(word in text for word in ("убед", "обман", "запуг", "уговор", "выступ", "очаров", "соврат", "торг")):
        return "CHA"
    if any(word in text for word in ("выдерж", "терп", "устоять", "устоят", "долго", "изнур")):
        return "CON"
    return "WIS"


def _ability_for_fields(fields: dict[str, str]) -> str:
    from AI import dnd_combat as combat

    explicit = str(fields.get("ABILITY") or "").upper()
    if explicit in ABILITY_LABELS:
        return explicit

    roll_type = str(fields.get("TYPE") or "CHECK").upper()
    reason = fields.get("REASON") or "проверка по ситуации"
    if roll_type == "SAVE":
        return combat._infer_save_ability(reason)

    skill = str(fields.get("SKILL") or "").casefold()
    for name, ability in combat.SKILL_ABILITIES.items():
        if name.casefold() == skill:
            return ability
    return _infer_check_ability(reason)


def annotate_roll_ability(response: str) -> tuple[str, str | None]:
    """Inject ABILITY and a human-readable ability label into the roll reason."""
    text = str(response or "")
    match = _ROLL_RE.search(text)
    if not match:
        return text, None
    raw = match.group(1)
    fields = _fields(raw)
    ability = _ability_for_fields(fields)
    label = ABILITY_LABELS[ability]

    parts = [part.strip() for part in raw.split(";") if part.strip()]
    has_ability = any(part.upper().startswith("ABILITY:") for part in parts)
    if not has_ability:
        parts.append(f"ABILITY:{ability}")

    reason_index = next((i for i, part in enumerate(parts) if part.upper().startswith("REASON:")), None)
    if reason_index is None:
        parts.append(f"REASON:проверка по ситуации · {label}")
    else:
        key, _, reason = parts[reason_index].partition(":")
        if label.casefold() not in reason.casefold():
            parts[reason_index] = f"{key}:{reason.strip()} · {label}"

    replacement = "[ACTION:ROLL;" + ";".join(parts) + "]"
    return text[: match.start()] + replacement + text[match.end() :], ability


def install_dnd_roll_ability_display(dnd) -> None:
    if getattr(dnd, "_upupa_dnd_roll_ability_display_installed", False):
        return

    if ROLL_ABILITY_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = (
            dnd.DND_SYSTEM_PROMPT.rstrip()
            + "\n\n"
            + ROLL_ABILITY_MARKER
            + ": у каждого ACTION:ROLL всегда указывай ABILITY:STR/DEX/CON/INT/WIS/CHA по смыслу действия или опасности."
        )

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if session and dnd._is_participant_mode(session):
            response, _ability = annotate_roll_ability(response)
        return await original_parse(bot, chat_id, response)

    dnd.parse_and_execute_turn = parse_turn
    dnd._upupa_dnd_roll_ability_display_installed = True


__all__ = [
    "ABILITY_LABELS",
    "annotate_roll_ability",
    "install_dnd_roll_ability_display",
]
