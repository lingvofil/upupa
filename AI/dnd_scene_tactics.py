"""Interactive scene objects and telegraphed enemy intents for participant DnD."""
from __future__ import annotations

import logging
import re


SCENE_TACTICS_MARKER = "ТАКТИЧЕСКОЕ ОКРУЖЕНИЕ DND УПУПЫ"
SCENE_TACTICS_RULES = f"""
{SCENE_TACTICS_MARKER}.
В значимых сценах можешь хранить 2–4 заметных интерактивных объекта окружения:
[SCENE:UPSERT;ID:люстра;NAME:тяжёлая люстра;STATE:висит над проходом;DETAIL:цепь доступна с балкона]
Изменение:
[SCENE:UPDATE;ID:люстра;STATE:лежит поперёк прохода;DETAIL:перекрывает выход;AVAILABLE:0]
Это не полный перечень мира. Если игрок спрашивает, есть ли правдоподобный объект, сначала установи факт через SCENE:UPSERT,
а затем разрешай действие. Не дорисовывай удобный смертельный объект задним числом.
Для ACTION:CINEMATIC_ATTACK обязательно указывай OBJECT:<ID> существующего или только что установленного доступного объекта.
После успешного смертельного манёвра код пометит этот объект использованным, поэтому повторить тот же трюк нельзя.

Для серьёзного врага можно заранее показать одно намерение:
[INTENT:SET;ENEMY:Каменный мясник;ACTION:обрушить колонну на балкон;TARGETS:123,456;DANGER:DEADLY;DETAIL:под угрозой все наверху]
В том же ответе открой партии INPUT или POLL и дай одну понятную возможность вмешаться.
Намерение не исполняется от случайного сообщения или прошедшего времени. Окно реакции закрывается только после
разрешения следующего заявленного действия партии. Сначала разреши это действие; если ему нужен бросок — дождись броска.
Затем либо сорви/измени намерение через [INTENT:CLEAR;REASON:...], либо исполни подготовленное последствие.
Сорванное намерение — уже выигранное преимущество: не выдавай тут же равноценную бесплатную атаку взамен.
""".strip()

_META_RE = re.compile(r"\[(SCENE|INTENT):([^\]]*)\]", re.I)
_ACTION_RE = re.compile(r"\[ACTION:([A-Z_]+)([^\]]*)\]", re.I | re.S)
_OBJECT_RE = re.compile(r"(?:^|;)OBJECT:([^;\]]+)(?=;|$)", re.I)
_TARGETS_RE = re.compile(r"(?:^|;)TARGETS:([0-9,\s]+)(?=;|$)", re.I)


def _fields(raw: str) -> tuple[str, dict[str, str]]:
    parts = [part.strip() for part in str(raw or "").split(";") if part.strip()]
    head = parts[0] if parts else ""
    fields = {}
    for part in parts[1:]:
        key, sep, value = part.partition(":")
        if sep and value.strip():
            fields[key.strip().upper()] = value.strip()
    return head, fields


def _clean(value, limit=180) -> str:
    return " ".join(str(value or "").replace("[", "").replace("]", "").split()).strip(" ;")[:limit]


def _clean_id(value) -> str:
    text = _clean(value, 80).casefold().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9_-]+", "-", text, flags=re.I).strip("-_")[:64]


def _participant_ids(session) -> set[str]:
    result = set()
    for key, row in (getattr(session, "participants", {}) or {}).items():
        try:
            result.add(str(int(row.get("user_id", key))))
        except (TypeError, ValueError):
            continue
    return result


def _ensure(session) -> None:
    if not isinstance(getattr(session, "scene_objects", None), dict):
        session.scene_objects = {}
    if not isinstance(getattr(session, "enemy_intent", None), dict):
        session.enemy_intent = None
    if not hasattr(session, "pending_scene_object_id"):
        session.pending_scene_object_id = None


def _restore(session, data) -> None:
    row = data if isinstance(data, dict) else {}
    session.scene_objects = {
        str(key): dict(value)
        for key, value in (row.get("scene_objects") or {}).items()
        if isinstance(value, dict)
    }
    intent = row.get("enemy_intent")
    session.enemy_intent = dict(intent) if isinstance(intent, dict) else None
    session.pending_scene_object_id = row.get("pending_scene_object_id")
    _ensure(session)


def _upsert_object(session, head: str, fields: dict[str, str]) -> None:
    object_id = _clean_id(fields.get("ID") or fields.get("NAME"))
    if not object_id:
        return
    if head.upper() == "REMOVE":
        session.scene_objects.pop(object_id, None)
        return
    if head.upper() not in {"UPSERT", "UPDATE"}:
        return
    current = dict(session.scene_objects.get(object_id) or {})
    raw_available = str(fields.get("AVAILABLE") or "").casefold()
    available = current.get("available", True)
    if raw_available in {"0", "false", "no", "нет"}:
        available = False
    elif raw_available in {"1", "true", "yes", "да"}:
        available = True
    session.scene_objects[object_id] = {
        "id": object_id,
        "name": _clean(fields.get("NAME") or current.get("name") or object_id, 90),
        "state": _clean(fields.get("STATE") or current.get("state"), 160),
        "detail": _clean(fields.get("DETAIL") or current.get("detail"), 180),
        "available": bool(available),
        "updated_scene": int(getattr(session, "scene_count", 0) or 0),
    }
    if len(session.scene_objects) > 8:
        ordered = sorted(session.scene_objects.values(), key=lambda item: int(item.get("updated_scene", 0) or 0))
        for item in ordered[:-8]:
            session.scene_objects.pop(str(item.get("id")), None)


def _set_intent(session, fields: dict[str, str]) -> None:
    enemy = _clean(fields.get("ENEMY"), 90)
    action = _clean(fields.get("ACTION"), 180)
    if not enemy or not action:
        return
    valid = _participant_ids(session)
    targets = []
    for raw in str(fields.get("TARGETS") or "").split(","):
        token = raw.strip()
        if token.isdigit() and token in valid:
            targets.append(int(token))
    danger = str(fields.get("DANGER") or "HIGH").upper()
    if danger not in {"LOW", "MEDIUM", "HIGH", "DEADLY"}:
        danger = "HIGH"
    session.enemy_intent = {
        "enemy": enemy,
        "action": action,
        "target_user_ids": targets,
        "danger": danger,
        "detail": _clean(fields.get("DETAIL"), 180),
        "due": False,
        "set_scene": int(getattr(session, "scene_count", 0) or 0),
    }


def _clear_intent(session, fields: dict[str, str] | None = None) -> str | None:
    intent = getattr(session, "enemy_intent", None)
    if not isinstance(intent, dict):
        return None
    enemy = intent.get("enemy") or "врага"
    reason = _clean((fields or {}).get("REASON"), 180)
    session.enemy_intent = None
    return f"✅ Намерение {enemy} сорвано" + (f": {reason}." if reason else ".")


def apply_scene_metadata(session, original_text: str, cleaned: str, notices: list[str]):
    _ensure(session)
    extra = []
    for match in _META_RE.finditer(str(original_text or "")):
        kind = match.group(1).upper()
        head, fields = _fields(match.group(2))
        if kind == "SCENE":
            _upsert_object(session, head, fields)
        elif head.upper() == "SET":
            _set_intent(session, fields)
        elif head.upper() == "CLEAR":
            notice = _clear_intent(session, fields)
            if notice:
                extra.append(notice)
    return _META_RE.sub("", str(cleaned or "")).strip(), list(notices or []) + extra


def _targets(suffix: str) -> list[int]:
    match = _TARGETS_RE.search(";" + str(suffix or "").strip(";") + ";")
    if not match:
        return []
    return [int(value) for value in match.group(1).split(",") if value.strip().isdigit()]


def _prospective_object(response: str, object_id: str) -> bool:
    wanted = _clean_id(object_id)
    for match in _META_RE.finditer(str(response or "")):
        if match.group(1).upper() != "SCENE":
            continue
        head, fields = _fields(match.group(2))
        if head.upper() not in {"UPSERT", "UPDATE"}:
            continue
        if _clean_id(fields.get("ID") or fields.get("NAME")) != wanted:
            continue
        return str(fields.get("AVAILABLE") or "1").casefold() not in {"0", "false", "no", "нет"}
    return False


def validate_cinematic_object(session, response: str) -> tuple[bool, str | None]:
    match = _ACTION_RE.search(str(response or ""))
    if not match or match.group(1).upper() != "CINEMATIC_ATTACK":
        return True, None
    object_match = _OBJECT_RE.search(";" + (match.group(2) or "").strip(";") + ";")
    if not object_match:
        return False, None
    object_id = _clean_id(object_match.group(1))
    _ensure(session)
    row = session.scene_objects.get(object_id)
    if isinstance(row, dict) and bool(row.get("available", True)):
        return True, object_id
    return _prospective_object(response, object_id), object_id


def _downgrade_invalid_cinematic(response: str) -> str:
    text = str(response or "")
    match = _ACTION_RE.search(text)
    if not match or match.group(1).upper() != "CINEMATIC_ATTACK":
        return text
    suffix = match.group(2) or ""
    targets = _targets(suffix)
    reason = re.search(r"(?:^|;)REASON:([^;\]]+)", ";" + suffix.strip(";") + ";", re.I)
    dc = re.search(r"(?:^|;)DC:(\d+)", ";" + suffix.strip(";") + ";", re.I)
    ability = re.search(r"(?:^|;)ABILITY:(STR|DEX|CON|INT|WIS|CHA)", ";" + suffix.strip(";") + ";", re.I)
    mode = re.search(r"(?:^|;)MODE:(NORMAL|ADVANTAGE|DISADVANTAGE)", ";" + suffix.strip(";") + ";", re.I)
    pieces = [
        "TYPE:CHECK",
        "DOMAIN:COMBAT",
        f"REASON:{_clean(reason.group(1) if reason else 'рискованный трюк без закреплённого объекта', 220)}",
        f"DC:{dc.group(1) if dc else '14'}",
        f"ABILITY:{ability.group(1).upper() if ability else 'STR'}",
        f"MODE:{mode.group(1).upper() if mode else 'NORMAL'}",
    ]
    if targets:
        pieces.append("TARGETS:" + str(targets[0]))
    if re.search(r"(?:^|;)SPECIAL:(?:1|TRUE|YES)(?=;|$)", ";" + suffix.strip(";") + ";", re.I):
        pieces.append("SPECIAL:1")
    replacement = "[ACTION:ROLL;" + ";".join(pieces) + "]"
    return text[:match.start()] + replacement + text[match.end():]


def consume_scene_object(session, object_id: str | None, method: str | None = None) -> None:
    _ensure(session)
    key = _clean_id(object_id)
    row = session.scene_objects.get(key)
    if not key or not isinstance(row, dict):
        return
    row["available"] = False
    row["state"] = "использован в манёвре"
    method = _clean(method, 140)
    row["detail"] = f"использован в манёвре: {method}" if method else "уже использован"
    row["updated_scene"] = int(getattr(session, "scene_count", 0) or 0)


def _context(session) -> str:
    _ensure(session)
    blocks = [SCENE_TACTICS_RULES]
    objects = sorted(
        session.scene_objects.values(),
        key=lambda item: int(item.get("updated_scene", 0) or 0),
    )[-4:]
    if objects:
        blocks.append(
            "ОБЪЕКТЫ СЦЕНЫ:\n" + "\n".join(
                f"- {item.get('id')}: {item.get('name')} — {item.get('state') or 'состояние не уточнено'}"
                + (f"; {item.get('detail')}" if item.get("detail") else "")
                + f" [{'доступен' if item.get('available', True) else 'уже использован'}]"
                for item in objects
            )
        )
    intent = session.enemy_intent
    if isinstance(intent, dict):
        targets = []
        for user_id in intent.get("target_user_ids") or []:
            row = (getattr(session, "participants", {}) or {}).get(str(int(user_id)), {})
            targets.append(row.get("name") or str(user_id))
        blocks.append(
            "НАМЕРЕНИЕ ВРАГА:\n"
            f"- {intent.get('enemy')}: {intent.get('action')}; опасность={intent.get('danger')}; "
            f"цели={', '.join(targets) or 'сцена'}"
            + (f"; {intent.get('detail')}" if intent.get("detail") else "")
            + (
                "\nОкно реакции закрывается: сначала разреши заявленное действие/его бросок, затем сорви или исполни намерение."
                if intent.get("due")
                else "\nУ партии ещё есть одно установленное окно вмешательства."
            )
        )
    return "\n".join(blocks)


def install_dnd_scene_tactics(dnd, *, state_policy, metadata_policy) -> None:
    from AI import dnd_campaign as campaign
    from AI import dnd_cinematic_combat as cinematic

    if getattr(dnd, "_upupa_dnd_scene_tactics_installed", False):
        return

    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field("scene_objects", lambda session: dict(getattr(session, "scene_objects", {}) or {}))
    state_policy.add_state_field("enemy_intent", lambda session: getattr(session, "enemy_intent", None))
    state_policy.add_state_field("pending_scene_object_id", lambda session: getattr(session, "pending_scene_object_id", None))
    state_policy.add_restore_hook(_restore)
    metadata_policy.add_postprocessor(apply_scene_metadata)

    if SCENE_TACTICS_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + SCENE_TACTICS_RULES

    original_context = campaign._campaign_context
    campaign._campaign_context = lambda dnd_module, session: original_context(dnd_module, session) + "\n" + _context(session)

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if not session or not dnd._is_participant_mode(session):
            return await original_parse(bot, chat_id, response)

        guarded = str(response or "")
        valid_object, object_id = validate_cinematic_object(session, guarded)
        if not valid_object:
            logging.warning(
                "DnD downgraded cinematic attack without available scene object chat_id=%s object_id=%s",
                chat_id, object_id,
            )
            guarded = _downgrade_invalid_cinematic(guarded)
            object_id = None

        intent_before = getattr(session, "enemy_intent", None)
        action_match = _ACTION_RE.search(guarded)
        action_name = action_match.group(1).upper() if action_match else None
        setting_intent = any(
            match.group(1).upper() == "INTENT" and _fields(match.group(2))[0].upper() == "SET"
            for match in _META_RE.finditer(guarded)
        )
        if action_name == "ENEMY_ATTACK" and (
            (isinstance(intent_before, dict) and not intent_before.get("due")) or setting_intent
        ):
            logging.warning("DnD blocked enemy attack before intent response window chat_id=%s", chat_id)
            guarded = _ACTION_RE.sub("[ACTION:INPUT]", guarded, count=1)
            action_name = "INPUT"

        result = await original_parse(bot, chat_id, guarded)

        if object_id and getattr(session, "state", None) == "WAITING_ROLL":
            pending = getattr(session, "pending_roll", None)
            if isinstance(pending, dict) and str(pending.get("type") or "").upper() == "CINEMATIC_ATTACK":
                pending.setdefault("cinematic", {})["tactical_scene_object_id"] = object_id
                session.pending_scene_object_id = object_id

        if isinstance(intent_before, dict) and intent_before.get("due") and action_name == "ENEMY_ATTACK":
            session.enemy_intent = None

        dnd.persist_dnd_sessions()
        return result

    dnd.parse_and_execute_turn = parse_turn

    original_finalize = dnd.finalize_group_actions

    async def finalize_group_actions(bot, chat_id, prompt_message_id):
        session = dnd.dnd_sessions.get(chat_id)
        if session and dnd._is_participant_mode(session):
            # Asynchronous Telegram play must not punish wall-clock delay.
            session.action_opened_at = None
            if isinstance(getattr(session, "enemy_intent", None), dict) and not session.enemy_intent.get("due"):
                session.enemy_intent["due"] = True
            dnd.persist_dnd_sessions()
        return await original_finalize(bot, chat_id, prompt_message_id)

    dnd.finalize_group_actions = finalize_group_actions

    original_resolve = cinematic._resolve_cinematic_mechanics

    def resolve_cinematic_mechanics(combat, player_combat, session, user_id, pending, natural):
        action = pending.get("cinematic") or {}
        object_id = action.get("tactical_scene_object_id")
        player_combat._ensure_enemy_store(session)
        enemy = session.enemy_combatants.get(str(action.get("enemy_key") or ""))
        before_dead = bool(
            isinstance(enemy, dict)
            and (enemy.get("status") == "dead" or int(enemy.get("hp", 0)) <= 0)
        )
        result = original_resolve(combat, player_combat, session, user_id, pending, natural)
        after_dead = bool(
            isinstance(enemy, dict)
            and (enemy.get("status") == "dead" or int(enemy.get("hp", 0)) <= 0)
        )
        if object_id and not before_dead and after_dead:
            consume_scene_object(session, object_id, action.get("method"))
        if object_id:
            session.pending_scene_object_id = None
        return result

    cinematic._resolve_cinematic_mechanics = resolve_cinematic_mechanics
    dnd._upupa_dnd_scene_tactics_installed = True


__all__ = [
    "SCENE_TACTICS_MARKER", "SCENE_TACTICS_RULES",
    "apply_scene_metadata", "validate_cinematic_object",
    "consume_scene_object", "install_dnd_scene_tactics",
]
