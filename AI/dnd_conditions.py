"""Persistent, bounded conditions for participant-mode Upupa DnD."""
from __future__ import annotations

import re


CONDITIONS_MARKER = "СОСТОЯНИЯ DND УПУПЫ"
CONDITION_RULES = f"""
{CONDITIONS_MARKER}.
Название состояния свободное, но механический EFFECT выбирай только из:
MOVE_DISADVANTAGE, SOCIAL_DISADVANTAGE, PERCEPTION_DISADVANTAGE, COMBAT_DISADVANTAGE,
NEXT_ROLL_DISADVANTAGE, ACTION_TO_CLEAR.
Не выдавай отдельное состояние просто из-за низкого HP: это не должно дважды наказывать за одну потерю здоровья.
Оглушение и похожий эффект не отнимают ход полностью: используй NEXT_ROLL_DISADVANTAGE или конкретное ограничение.
Добавление:
[CONDITION:ADD;PLAYER:123;NAME:подвернул ногу;EFFECT:MOVE_DISADVANTAGE;CLEAR:перевязать самому или получить помощь товарища;SCENES:2]
Снятие:
[CONDITION:REMOVE;PLAYER:123;NAME:подвернул ногу]
SCENES считает сюжетные сцены, USES — подходящие действия/броски. Реальное время и число сообщений не используй.
Одинаковый EFFECT у одного героя не складывай: новое состояние заменяет старое с тем же механическим эффектом.
Для ACTION:ROLL добавляй DOMAIN:MOVE/SOCIAL/PERCEPTION/COMBAT/OTHER по смыслу действия.
Код сам применит помеху от подходящего состояния. Не добавляй вторую помеху вручную.
""".strip()

_META_RE = re.compile(r"\[CONDITION:([^\]]*)\]", re.I)
_ACTION_RE = re.compile(r"\[ACTION:([A-Z_]+)([^\]]*)\]", re.I | re.S)
_TARGETS_RE = re.compile(r"(?:^|;)TARGETS:([0-9,\s]+)(?=;|$)", re.I)
_MODE_RE = re.compile(r"(?:^|;)MODE:([A-Z_]+)(?=;|$)", re.I)
_DOMAIN_RE = re.compile(r"(?:^|;)DOMAIN:([A-Z_]+)(?=;|$)", re.I)

EFFECTS = {
    "MOVE_DISADVANTAGE": "помеха на бег, прыжки и активную мобильность",
    "SOCIAL_DISADVANTAGE": "помеха на социальные проверки",
    "PERCEPTION_DISADVANTAGE": "помеха на наблюдение и поиск",
    "COMBAT_DISADVANTAGE": "помеха на боевые проверки и атаки",
    "NEXT_ROLL_DISADVANTAGE": "помеха на следующий подходящий бросок",
    "ACTION_TO_CLEAR": "нужно отдельное осмысленное действие для снятия",
}
_DOMAIN_EFFECT = {
    "MOVE": "MOVE_DISADVANTAGE",
    "SOCIAL": "SOCIAL_DISADVANTAGE",
    "PERCEPTION": "PERCEPTION_DISADVANTAGE",
    "COMBAT": "COMBAT_DISADVANTAGE",
}


def _fields(raw: str) -> tuple[str, dict[str, str]]:
    parts = [part.strip() for part in str(raw or "").split(";") if part.strip()]
    head = parts[0] if parts else ""
    result = {}
    for part in parts[1:]:
        key, sep, value = part.partition(":")
        if sep and value.strip():
            result[key.strip().upper()] = value.strip()
    return head, result


def _clean(value, limit=180) -> str:
    return " ".join(str(value or "").replace("[", "").replace("]", "").split()).strip(" ;")[:limit]


def _participant_ids(session) -> set[str]:
    result = set()
    for key, row in (getattr(session, "participants", {}) or {}).items():
        try:
            result.add(str(int(row.get("user_id", key))))
        except (TypeError, ValueError):
            continue
    return result


def _ensure(session) -> None:
    if not isinstance(getattr(session, "conditions", None), dict):
        session.conditions = {}


def _restore(session, data) -> None:
    raw = (data or {}).get("conditions") if isinstance(data, dict) else None
    session.conditions = {
        str(key): [dict(item) for item in rows if isinstance(item, dict)]
        for key, rows in (raw or {}).items()
        if isinstance(rows, list)
    }
    _ensure(session)


def _rows(session, player: str) -> list[dict]:
    _ensure(session)
    return session.conditions.setdefault(str(player), [])


def _add(session, fields: dict[str, str]) -> str | None:
    player = str(fields.get("PLAYER") or "")
    name = _clean(fields.get("NAME"), 80)
    effect = str(fields.get("EFFECT") or "").upper()
    if player not in _participant_ids(session) or not name or effect not in EFFECTS:
        return None
    try:
        scenes = max(0, min(6, int(fields.get("SCENES", 0))))
    except (TypeError, ValueError):
        scenes = 0
    try:
        uses = max(0, min(3, int(fields.get("USES", 0))))
    except (TypeError, ValueError):
        uses = 0
    item = {
        "name": name,
        "effect": effect,
        "clear": _clean(fields.get("CLEAR"), 180),
        "scenes_remaining": scenes or None,
        "uses_remaining": uses or None,
        "skip_scene_tick": int(getattr(session, "scene_count", 0) or 0) + 1 if scenes else None,
    }
    rows = _rows(session, player)
    rows[:] = [row for row in rows if str(row.get("effect") or "").upper() != effect]
    rows.append(item)
    person = (getattr(session, "participants", {}) or {}).get(player, {})
    name = person.get("name") or f"игрок {player}"
    return f"🩹 {name}: {item['name']} — {EFFECTS[effect]}."


def _remove(session, fields: dict[str, str]) -> str | None:
    player = str(fields.get("PLAYER") or "")
    if player not in _participant_ids(session):
        return None
    wanted_name = _clean(fields.get("NAME"), 80).casefold()
    wanted_effect = str(fields.get("EFFECT") or "").upper()
    kept, removed = [], []
    for item in _rows(session, player):
        same_name = bool(wanted_name and str(item.get("name") or "").casefold() == wanted_name)
        same_effect = bool(wanted_effect and str(item.get("effect") or "").upper() == wanted_effect)
        if same_name or same_effect or (not wanted_name and not wanted_effect):
            removed.append(item)
        else:
            kept.append(item)
    session.conditions[player] = kept
    if not removed:
        return None
    person = (getattr(session, "participants", {}) or {}).get(player, {})
    who = person.get("name") or f"игрок {player}"
    labels = ", ".join(str(item.get("name") or "состояние") for item in removed[:2])
    return f"✅ {who}: снято состояние — {labels}."


def apply_condition_metadata(session, original_text: str, cleaned: str, notices: list[str]):
    _ensure(session)
    extra = []
    for match in _META_RE.finditer(str(original_text or "")):
        head, fields = _fields(match.group(1))
        if head.upper() == "ADD":
            notice = _add(session, fields)
        elif head.upper() == "REMOVE":
            notice = _remove(session, fields)
        else:
            notice = None
        if notice:
            extra.append(notice)
    return _META_RE.sub("", str(cleaned or "")).strip(), list(notices or []) + extra


def _targets(suffix: str) -> list[int]:
    match = _TARGETS_RE.search(";" + str(suffix or "").strip(";") + ";")
    if not match:
        return []
    return [int(x) for x in match.group(1).split(",") if x.strip().isdigit()]


def _mode(suffix: str) -> str:
    match = _MODE_RE.search(";" + str(suffix or "").strip(";") + ";")
    value = match.group(1).upper() if match else "NORMAL"
    return value if value in {"NORMAL", "ADVANTAGE", "DISADVANTAGE"} else "NORMAL"


def _domain(action: str, suffix: str) -> str:
    if action in {"PLAYER_ATTACK", "CINEMATIC_ATTACK"}:
        return "COMBAT"
    match = _DOMAIN_RE.search(";" + str(suffix or "").strip(";") + ";")
    return match.group(1).upper() if match else "OTHER"


def _combine_mode(current: str, disadvantage: bool) -> str:
    if not disadvantage:
        return current
    if current == "ADVANTAGE":
        return "NORMAL"
    return "DISADVANTAGE"


def _set_mode(response: str, mode: str) -> str:
    text = str(response or "")
    match = _ACTION_RE.search(text)
    if not match:
        return text
    action = match.group(1).upper()
    suffix = (match.group(2) or "").strip(";")
    normalized = f";{suffix}" if suffix else ""
    existing = re.search(r";MODE:[A-Z_]+(?=;|$)", normalized, re.I)
    if existing:
        normalized = normalized[:existing.start()] + f";MODE:{mode}" + normalized[existing.end():]
    else:
        normalized += f";MODE:{mode}"
    return text[:match.start()] + f"[ACTION:{action}{normalized}]" + text[match.end():]


def apply_condition_penalties(session, response: str) -> tuple[str, list[tuple[str, str]]]:
    _ensure(session)
    match = _ACTION_RE.search(str(response or ""))
    if not match or match.group(1).upper() not in {"ROLL", "PLAYER_ATTACK", "CINEMATIC_ATTACK"}:
        return str(response or ""), []
    action, suffix = match.group(1).upper(), match.group(2) or ""
    targets = _targets(suffix)
    if len(targets) != 1:
        return str(response or ""), []
    player = str(targets[0])
    domain = _domain(action, suffix)
    consumed = []
    disadvantage = False
    for item in list(_rows(session, player)):
        effect = str(item.get("effect") or "").upper()
        relevant = effect == "NEXT_ROLL_DISADVANTAGE" or _DOMAIN_EFFECT.get(domain) == effect
        if not relevant:
            continue
        disadvantage = True
        if item.get("uses_remaining") is not None or effect == "NEXT_ROLL_DISADVANTAGE":
            consumed.append((player, effect))
    if not disadvantage:
        return str(response or ""), []
    return _set_mode(response, _combine_mode(_mode(suffix), True)), consumed


_PENDING_USES_KEY = "condition_uses_pending"


def _queue_pending_uses(session, consumed: list[tuple[str, str]]) -> bool:
    pending = getattr(session, "pending_roll", None)
    if not isinstance(pending, dict) or not consumed:
        return False
    pending[_PENDING_USES_KEY] = [
        {"player": str(player), "effect": str(effect)}
        for player, effect in consumed
    ]
    return True


def _consume_pending_uses(session, pending: dict) -> bool:
    raw = pending.get(_PENDING_USES_KEY) if isinstance(pending, dict) else None
    consumed = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        player = str(item.get("player") or "")
        effect = str(item.get("effect") or "").upper()
        if player and effect:
            consumed.append((player, effect))
    if not consumed:
        return False
    _consume_uses(session, consumed)
    pending.pop(_PENDING_USES_KEY, None)
    return True


def _consume_uses(session, consumed: list[tuple[str, str]]) -> None:
    for player, effect in consumed:
        kept = []
        for item in _rows(session, player):
            if str(item.get("effect") or "").upper() != effect:
                kept.append(item)
                continue
            remaining = item.get("uses_remaining")
            if remaining is None:
                remaining = 1 if effect == "NEXT_ROLL_DISADVANTAGE" else None
            if remaining is None:
                kept.append(item)
                continue
            try:
                remaining = int(remaining) - 1
            except (TypeError, ValueError):
                remaining = 0
            if remaining > 0:
                item["uses_remaining"] = remaining
                kept.append(item)
        session.conditions[player] = kept


def advance_condition_scenes(session) -> None:
    _ensure(session)
    scene = int(getattr(session, "scene_count", 0) or 0)
    for player, rows in list(session.conditions.items()):
        kept = []
        for item in rows:
            remaining = item.get("scenes_remaining")
            if remaining is None:
                kept.append(item)
                continue
            skip = item.get("skip_scene_tick")
            if skip is not None and scene <= int(skip):
                kept.append(item)
                continue
            try:
                remaining = int(remaining) - 1
            except (TypeError, ValueError):
                remaining = 0
            if remaining > 0:
                item["scenes_remaining"] = remaining
                kept.append(item)
        session.conditions[player] = kept


def _context(session) -> str:
    _ensure(session)
    lines = []
    for player, rows in session.conditions.items():
        person = (getattr(session, "participants", {}) or {}).get(str(player), {})
        who = person.get("name") or f"ID {player}"
        for item in rows:
            clear = f"; снять: {item.get('clear')}" if item.get("clear") else ""
            lines.append(f"- {who}: {item.get('name')} [{item.get('effect')}]{clear}")
    return CONDITION_RULES + (("\nАКТИВНЫЕ СОСТОЯНИЯ:\n" + "\n".join(lines[-10:])) if lines else "")


def install_dnd_conditions(dnd, *, state_policy, metadata_policy) -> None:
    from AI import dnd_campaign as campaign
    from AI import dnd_combat as combat
    if getattr(dnd, "_upupa_dnd_conditions_installed", False):
        return
    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field("conditions", lambda session: dict(getattr(session, "conditions", {}) or {}))
    state_policy.add_restore_hook(_restore)
    metadata_policy.add_postprocessor(apply_condition_metadata)

    if CONDITIONS_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + CONDITION_RULES

    original_context = campaign._campaign_context
    campaign._campaign_context = lambda dnd_module, session: original_context(dnd_module, session) + "\n" + _context(session)

    original_record = campaign._record_scene
    def record_scene(session, text):
        story = original_record(session, text)
        if story:
            advance_condition_scenes(session)
        return story
    campaign._record_scene = record_scene

    original_parse = dnd.parse_and_execute_turn
    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        consumed = []
        guarded = str(response or "")
        if session and dnd._is_participant_mode(session):
            guarded, consumed = apply_condition_penalties(session, guarded)
        result = await original_parse(bot, chat_id, guarded)
        if session and consumed and getattr(session, "state", None) == "WAITING_ROLL":
            if _queue_pending_uses(session, consumed):
                dnd.persist_dnd_sessions()
        return result
    dnd.parse_and_execute_turn = parse_turn

    original_resolve_player_roll = combat._resolve_player_roll

    async def resolve_player_roll(dnd_module, message, session):
        pending = getattr(session, "pending_roll", None)
        should_consume = bool(
            isinstance(pending, dict)
            and pending.get(_PENDING_USES_KEY)
        )
        await original_resolve_player_roll(dnd_module, message, session)
        if should_consume and getattr(session, "pending_roll", None) is not pending:
            if _consume_pending_uses(session, pending):
                dnd_module.persist_dnd_sessions()

    combat._resolve_player_roll = resolve_player_roll
    dnd._upupa_dnd_conditions_installed = True


__all__ = [
    "CONDITIONS_MARKER", "CONDITION_RULES", "EFFECTS",
    "apply_condition_metadata", "apply_condition_penalties",
    "advance_condition_scenes", "install_dnd_conditions",
]
