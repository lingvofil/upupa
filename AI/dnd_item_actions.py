"""Code-enforced active inventory tools for participant-mode Upupa DnD."""
from __future__ import annotations

import re
from copy import deepcopy

from aiogram import BaseMiddleware

from AI import dnd_inventory_fun as inventory_fun


ITEM_ACTIONS_MARKER = "АКТИВНЫЕ ПРЕДМЕТЫ DND УПУПЫ"
ITEM_ACTION_RULES = f"""
{ITEM_ACTIONS_MARKER}.
Некоторые значимые предметы и артефакты могут быть активными инструментами выбора. Их механика задаётся КОДОМ:
MECH — только из фиксированного набора, CHARGES — 1..3 применения за приключение, REQUIRE и COST — из фиксированных наборов.
Не делай механическим каждый найденный носок: такие свойства нужны редким, запоминающимся и сюжетно полезным вещам.

Допустимые MECH:
ADVANTAGE_MOVE — преимущество на следующую подходящую проверку движения/побега.
ADVANTAGE_SOCIAL — преимущество на следующую социальную проверку.
ADVANTAGE_PERCEPTION — преимущество на следующую проверку поиска/наблюдения.
ADVANTAGE_COMBAT — преимущество на следующую боевую проверку/атаку.
CLEAR_CONDITION — снять одно текущее состояние владельца; TARGET_EFFECT может уточнить механический эффект.
CREATE_EXIT — гарантированно создать физический выход/дверь в текущей сцене.
CLOCK_PUSH — сдвинуть подходящую активную шкалу на POWER (1..3); CLOCK_KIND или CLOCK_ID уточняет цель.
CLOCK_COMPLETE — полностью закрыть подходящую шкалу прямым решением; CLOCK_KIND или CLOCK_ID уточняет цель.
CLEAR_ACCUSATION — гарантированно снять одно актуальное обвинение с предъявителя как установленный факт сцены.

REQUIRE: NONE или CONFESS_FEAR. CONFESS_FEAR означает: игрок должен сам явно признаться в тексте своего действия, что ему страшно.
COST: NONE, DANGER_PLUS_1, DROP_ITEM, GAIN_CONDITION или NEXT_ACCUSATION_SELF.
GAIN_CONDITION требует COST_NAME, COST_EFFECT и желательно COST_CLEAR.
NEXT_ACCUSATION_SELF означает: следующее реально возникшее обвинение обязано лечь на владельца; этот долг хранит код, пока
ты не отыграешь его и не вернёшь [ITEMFACT:RESOLVE;PLAYER:123;KIND:NEXT_ACCUSATION_SELF].

Примеры:
[ITEM:ADD;PLAYER:123;NAME:Дверная ручка;KIND:artifact;EFFECT:раз за приключение делает дверь там где её не было;MECH:CREATE_EXIT;CHARGES:1;COST:DANGER_PLUS_1]
[ITEM:ADD;PLAYER:123;NAME:Медаль за трусость;KIND:artifact;EFFECT:помогает красиво смыться после честного признания;MECH:ADVANTAGE_MOVE;CHARGES:1;REQUIRE:CONFESS_FEAR;COST:NONE]
[ITEM:ADD;PLAYER:123;NAME:Справка о невиновности;KIND:artifact;EFFECT:юридически сомнительно но убедительно снимает обвинение;MECH:CLEAR_ACCUSATION;CHARGES:2;COST:NEXT_ACCUSATION_SELF]

Игрок применяет активную вещь своим обычным действием, начиная фразу с «использую <название>» или «применяю <название>».
Код сам проверит владение, заряд и условие, спишет заряд после успешной генерации и применит MECH/COST.
Не выдумывай второй эффект сверх указанного. Кодовые notice/факты считаются истиной сцены.
""".strip()

_ITEMFACT_RE = re.compile(r"\[ITEMFACT:([^\]]*)\]", re.I)
_ACTION_RE = re.compile(r"\[ACTION:([A-Z_]+)([^\]]*)\]", re.I | re.S)
_TARGETS_RE = re.compile(r"(?:^|;)TARGETS:([0-9,\s]+)(?=;|$)", re.I)
_MODE_RE = re.compile(r"(?:^|;)MODE:([A-Z_]+)(?=;|$)", re.I)
_DOMAIN_RE = re.compile(r"(?:^|;)DOMAIN:([A-Z_]+)(?=;|$)", re.I)

VALID_MECH = {
    "ADVANTAGE_MOVE",
    "ADVANTAGE_SOCIAL",
    "ADVANTAGE_PERCEPTION",
    "ADVANTAGE_COMBAT",
    "CLEAR_CONDITION",
    "CREATE_EXIT",
    "CLOCK_PUSH",
    "CLOCK_COMPLETE",
    "CLEAR_ACCUSATION",
}
VALID_REQUIREMENTS = {"NONE", "CONFESS_FEAR"}
VALID_COSTS = {"NONE", "DANGER_PLUS_1", "DROP_ITEM", "GAIN_CONDITION", "NEXT_ACCUSATION_SELF"}
_ADVANTAGE_DOMAINS = {
    "ADVANTAGE_MOVE": "MOVE",
    "ADVANTAGE_SOCIAL": "SOCIAL",
    "ADVANTAGE_PERCEPTION": "PERCEPTION",
    "ADVANTAGE_COMBAT": "COMBAT",
}
_MECH_LABELS = {
    "ADVANTAGE_MOVE": "преимущество на движение/побег",
    "ADVANTAGE_SOCIAL": "преимущество на социальную проверку",
    "ADVANTAGE_PERCEPTION": "преимущество на поиск/наблюдение",
    "ADVANTAGE_COMBAT": "преимущество на боевую проверку",
    "CLEAR_CONDITION": "снимает одно подходящее состояние",
    "CREATE_EXIT": "создаёт физический выход в сцене",
    "CLOCK_PUSH": "двигает активную шкалу",
    "CLOCK_COMPLETE": "закрывает подходящую шкалу прямым решением",
    "CLEAR_ACCUSATION": "снимает одно актуальное обвинение",
}
_REQUIREMENT_LABELS = {
    "CONFESS_FEAR": "нужно честно признаться, что страшно",
}
_COST_LABELS = {
    "DANGER_PLUS_1": "цена: опасность +1",
    "DROP_ITEM": "цена: предмет исчезает",
    "GAIN_CONDITION": "цена: получишь состояние",
    "NEXT_ACCUSATION_SELF": "цена: следующее обвинение — на тебя",
}
_USE_PREFIXES = ("использую ", "применяю ")
_FEAR_RE = re.compile(
    r"\b(?:боюсь|боязно|страшн\w*|ссык\w*|ссы\w*|трус\w*|паник\w*|очкую|очк\w*)\b",
    re.I,
)


def _clean(value, limit=220) -> str:
    return " ".join(str(value or "").replace("[", "").replace("]", "").split()).strip(" ;")[:limit]


def _safe_int(value, default: int, low: int, high: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def _mechanic(item) -> str:
    if not isinstance(item, dict):
        return ""
    value = str(item.get("mechanic") or "").upper()
    return value if value in VALID_MECH else ""


def _requirement(item) -> str:
    if not isinstance(item, dict):
        return "NONE"
    value = str(item.get("requirement") or "NONE").upper()
    return value if value in VALID_REQUIREMENTS else "NONE"


def _cost(item) -> str:
    if not isinstance(item, dict):
        return "NONE"
    value = str(item.get("cost") or "NONE").upper()
    return value if value in VALID_COSTS else "NONE"


def _charges_max(item) -> int:
    if not isinstance(item, dict) or not _mechanic(item):
        return 0
    return _safe_int(item.get("charges_max"), 1, 1, 3)


def _charges_remaining(item) -> int:
    maximum = _charges_max(item)
    if maximum <= 0:
        return 0
    return _safe_int(item.get("charges_remaining"), maximum, 0, maximum)


def _valid_players(session) -> set[str]:
    result = set()
    for key, row in (getattr(session, "participants", {}) or {}).items():
        try:
            result.add(str(int(row.get("user_id", key))))
        except (TypeError, ValueError):
            continue
    return result


def _ensure(session) -> None:
    if not isinstance(getattr(session, "pending_item_uses", None), dict):
        session.pending_item_uses = {}
    if not isinstance(getattr(session, "item_boosts", None), dict):
        session.item_boosts = {}
    if not isinstance(getattr(session, "item_world_facts", None), list):
        session.item_world_facts = []


def _restore(session, data) -> None:
    row = data if isinstance(data, dict) else {}
    session.pending_item_uses = {
        str(key): dict(value)
        for key, value in (row.get("pending_item_uses") or {}).items()
        if isinstance(value, dict)
    }
    session.item_boosts = {
        str(key): dict(value)
        for key, value in (row.get("item_boosts") or {}).items()
        if isinstance(value, dict)
    }
    session.item_world_facts = [
        dict(value) for value in (row.get("item_world_facts") or [])[-12:]
        if isinstance(value, dict)
    ]
    _ensure(session)


def _find_item(session, user_id: int, item_name: str):
    inventory = (getattr(session, "inventories", {}) or {}).get(str(int(user_id)), [])
    index = inventory_fun._find_transfer_item_index(inventory, item_name)
    if index is None:
        return None, None
    return index, inventory[index]


def _stem_token(value: str) -> str:
    token = re.sub(r"[^a-zа-я0-9_-]+", "", str(value or "").casefold().replace("ё", "е"), flags=re.I)
    if len(token) <= 4:
        return token
    for ending in (
        "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими",
        "ую", "юю", "ая", "яя", "ой", "ей", "ам", "ям", "ах", "ях",
        "ов", "ев", "ом", "ем", "ы", "и", "а", "я", "у", "ю", "е", "о",
    ):
        if token.endswith(ending) and len(token) - len(ending) >= 3:
            return token[:-len(ending)]
    return token


def _name_prefix_match(remainder: str, alias: str) -> tuple[bool, int]:
    rest_tokens = [token for token in str(remainder or "").split() if token]
    alias_tokens = [token for token in str(alias or "").split() if token]
    if not alias_tokens or len(rest_tokens) < len(alias_tokens):
        return False, 0
    rest_stems = [_stem_token(token) for token in rest_tokens[: len(alias_tokens)]]
    alias_stems = [_stem_token(token) for token in alias_tokens]
    return rest_stems == alias_stems, len(alias_tokens)


def _parse_item_use(session, user_id: int, text: str):
    normalized = " ".join(str(text or "").strip().casefold().split())
    remainder = None
    for prefix in _USE_PREFIXES:
        if normalized.startswith(prefix):
            remainder = normalized[len(prefix):].strip()
            break
    if not remainder:
        return None

    items = (getattr(session, "inventories", {}) or {}).get(str(int(user_id)), [])
    candidates = []
    for index, item in enumerate(items):
        if not _mechanic(item):
            continue
        for alias in inventory_fun._item_aliases(item):
            matched, token_count = _name_prefix_match(remainder, alias)
            if matched:
                candidates.append((token_count, len(alias), index, item))
    if not candidates:
        return None
    token_count, _, index, item = max(candidates, key=lambda row: (row[0], row[1]))
    rest_tokens = remainder.split()
    tail = " ".join(rest_tokens[token_count:]).strip(" —:-")
    return {
        "index": index,
        "name": inventory_fun._name(item),
        "mechanic": _mechanic(item),
        "requirement": _requirement(item),
        "cost": _cost(item),
        "text": str(text or "").strip(),
        "tail": tail,
    }


def _requirement_ok(plan: dict) -> tuple[bool, str | None]:
    requirement = str(plan.get("requirement") or "NONE").upper()
    if requirement == "CONFESS_FEAR" and not _FEAR_RE.search(str(plan.get("tail") or "")):
        return False, "Эта штука работает только если в том же действии честно признаться, что тебе страшно."
    return True, None


def _select_clock(session, item):
    from AI import dnd_scene_clocks as clocks

    clocks._ensure(session)
    wanted_id = str(item.get("clock_id") or "").strip().casefold()
    wanted_kind = str(item.get("clock_kind") or "").upper()
    if wanted_id:
        return session.scene_clocks.get(wanted_id)
    if wanted_kind:
        for row in session.scene_clocks.values():
            if str(row.get("kind") or "").upper() == wanted_kind and not row.get("full"):
                return row
    for row in session.scene_clocks.values():
        if not row.get("full"):
            return row
    return None


def _condition_target(session, user_id: int, item):
    from AI import dnd_conditions as conditions

    conditions._ensure(session)
    wanted = str(item.get("target_effect") or "").upper()
    rows = list((session.conditions or {}).get(str(int(user_id)), []) or [])
    if wanted:
        for row in rows:
            if str(row.get("effect") or "").upper() == wanted:
                return row
        return None
    return rows[0] if rows else None


def _prevalidate_plan(session, user_id: int, plan: dict | None) -> tuple[bool, str | None, dict | None]:
    _ensure(session)
    if not isinstance(plan, dict):
        return False, "Не смог распознать активный предмет в этой фразе.", None
    index = plan.get("index")
    items = (getattr(session, "inventories", {}) or {}).get(str(int(user_id)), [])
    if not isinstance(index, int) or not (0 <= index < len(items)):
        return False, "Предмет уже куда-то делся.", None
    item = items[index]
    if inventory_fun._name(item).casefold() != str(plan.get("name") or "").casefold() or not _mechanic(item):
        return False, "Механика предмета изменилась — заяви действие ещё раз.", None
    if _charges_remaining(item) <= 0:
        return False, f"У «{inventory_fun._name(item)}» на это приключение зарядов больше нет.", None

    ok, error = _requirement_ok(plan)
    if not ok:
        return False, error, None

    mechanic = _mechanic(item)
    if mechanic == "CLEAR_CONDITION" and _condition_target(session, user_id, item) is None:
        return False, "Сейчас у тебя нет подходящего состояния, которое эта вещь могла бы снять.", None
    if mechanic in {"CLOCK_PUSH", "CLOCK_COMPLETE"} and _select_clock(session, item) is None:
        return False, "Сейчас нет подходящей активной шкалы для этой вещи.", None

    fresh = dict(plan)
    fresh["item_snapshot"] = {
        "mechanic": mechanic,
        "requirement": _requirement(item),
        "cost": _cost(item),
        "power": _safe_int(item.get("power"), 1, 1, 3),
        "clock_id": str(item.get("clock_id") or ""),
        "clock_kind": str(item.get("clock_kind") or ""),
        "target_effect": str(item.get("target_effect") or ""),
        "cost_name": str(item.get("cost_name") or ""),
        "cost_effect": str(item.get("cost_effect") or ""),
        "cost_clear": str(item.get("cost_clear") or ""),
    }
    return True, None, fresh


def _plan_description(plan: dict) -> str:
    snap = plan.get("item_snapshot") or {}
    mechanic = str(snap.get("mechanic") or plan.get("mechanic") or "")
    cost = str(snap.get("cost") or plan.get("cost") or "NONE")
    bits = [f"«{plan.get('name')}»: {_MECH_LABELS.get(mechanic, mechanic)}"]
    if plan.get("requirement") in _REQUIREMENT_LABELS:
        bits.append(_REQUIREMENT_LABELS[plan["requirement"]])
    if cost in _COST_LABELS:
        bits.append(_COST_LABELS[cost])
    return "; ".join(bits)


def _append_item_action_note(action: str, plan: dict) -> str:
    return (
        str(action or "").rstrip()
        + "\n"
        + "ТЕХНИЧЕСКИ ПОДТВЕРЖДЕНО КОДОМ: игрок применяет активный предмет — "
        + _plan_description(plan)
        + ". Эффект гарантирован при успешной генерации этой сцены; не отменяй его произвольно."
    )


def _mechanics_prompt(session) -> str:
    _ensure(session)
    plans = [plan for plan in session.pending_item_uses.values() if isinstance(plan, dict)]
    if not plans:
        return ""
    return (
        "\n\nКОДОВЫЕ ПРИМЕНЕНИЯ ПРЕДМЕТОВ В ЭТОМ ХОДЕ:\n"
        + "\n".join(f"- ID {player}: {_plan_description(plan)}" for player, plan in session.pending_item_uses.items())
        + "\nОпиши эти эффекты как установленные механикой. Не отменяй заряд/цену и не добавляй предмету другой бонус."
    )


def _set_action_mode(response: str, mode: str) -> str:
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


def _targets(suffix: str) -> list[int]:
    match = _TARGETS_RE.search(";" + str(suffix or "").strip(";") + ";")
    if not match:
        return []
    return [int(value) for value in match.group(1).split(",") if value.strip().isdigit()]


def _mode(suffix: str) -> str:
    match = _MODE_RE.search(";" + str(suffix or "").strip(";") + ";")
    value = match.group(1).upper() if match else "NORMAL"
    return value if value in {"NORMAL", "ADVANTAGE", "DISADVANTAGE"} else "NORMAL"


def _domain(action: str, suffix: str) -> str:
    if action in {"PLAYER_ATTACK", "CINEMATIC_ATTACK"}:
        return "COMBAT"
    match = _DOMAIN_RE.search(";" + str(suffix or "").strip(";") + ";")
    return match.group(1).upper() if match else "OTHER"


def _combine_advantage(mode: str) -> str:
    if str(mode).upper() == "DISADVANTAGE":
        return "NORMAL"
    return "ADVANTAGE"


def apply_item_boost(session, response: str) -> tuple[str, str | None]:
    _ensure(session)
    match = _ACTION_RE.search(str(response or ""))
    if not match or match.group(1).upper() not in {"ROLL", "PLAYER_ATTACK", "CINEMATIC_ATTACK"}:
        return str(response or ""), None
    action, suffix = match.group(1).upper(), match.group(2) or ""
    targets = _targets(suffix)
    if len(targets) != 1:
        return str(response or ""), None
    player = str(targets[0])
    boost = session.item_boosts.get(player)
    if not isinstance(boost, dict):
        return str(response or ""), None
    if str(boost.get("domain") or "").upper() != _domain(action, suffix):
        return str(response or ""), None
    current_mode = _mode(suffix)
    if current_mode == "ADVANTAGE":
        return str(response or ""), None
    guarded = _set_action_mode(response, _combine_advantage(current_mode))
    source = str(boost.get("source") or "предмет")
    session.item_boosts.pop(player, None)
    return guarded, source


def _spend_charge(item) -> None:
    item["charges_remaining"] = max(0, _charges_remaining(item) - 1)


def _add_world_fact(session, *, kind: str, player: int, text: str) -> None:
    _ensure(session)
    normalized_kind = str(kind).upper()
    normalized_player = int(player)
    session.item_world_facts = [
        fact for fact in session.item_world_facts
        if not (
            str(fact.get("kind") or "").upper() == normalized_kind
            and int(fact.get("player") or 0) == normalized_player
        )
    ]
    session.item_world_facts.append({
        "kind": normalized_kind,
        "player": normalized_player,
        "text": _clean(text, 260),
        "scene": int(getattr(session, "scene_count", 0) or 0),
    })
    session.item_world_facts = session.item_world_facts[-12:]


def _apply_danger_cost(session, user_id: int, item_name: str) -> str:
    from AI import dnd_scene_clocks as clocks

    clocks._ensure(session)
    for row in session.scene_clocks.values():
        if str(row.get("kind") or "").upper() == "DANGER" and not row.get("full"):
            notice = clocks._delta_clock(
                session,
                {
                    "ID": str(row.get("id")),
                    "DELTA": "1",
                    "CAUSE": f"цена применения «{item_name}»",
                },
            )
            return notice or f"🚨 Цена «{item_name}» учтена."
    threat = getattr(session, "threat", None)
    if isinstance(threat, dict) and threat.get("name"):
        maximum = max(1, int(threat.get("max", 6) or 6))
        old = max(0, int(threat.get("level", 0) or 0))
        new = min(maximum, old + 1)
        threat["level"] = new
        return f"🚨 Цена «{item_name}»: {threat.get('name')} {new}/{maximum}."
    _add_world_fact(
        session,
        kind="ALERT",
        player=user_id,
        text=f"Цена применения «{item_name}»: противник/преследователь получает дополнительную зацепку или слышит последствия.",
    )
    return f"🚨 Цена «{item_name}»: опасность получила зацепку."


def _apply_cost(session, user_id: int, item, plan: dict) -> list[str]:
    from AI import dnd_conditions as conditions

    cost = _cost(item)
    name = inventory_fun._name(item)
    if cost == "NONE":
        return []
    if cost == "DANGER_PLUS_1":
        return [_apply_danger_cost(session, user_id, name)]
    if cost == "DROP_ITEM":
        inventory = session.inventories.get(str(int(user_id)), [])
        index = inventory_fun._find_item_index(inventory, name)
        if index is not None:
            inventory.pop(index)
        return [f"💸 Цена «{name}»: предмет исчезает после применения."]
    if cost == "NEXT_ACCUSATION_SELF":
        _add_world_fact(
            session,
            kind="NEXT_ACCUSATION_SELF",
            player=user_id,
            text=f"Следующее реально возникшее обвинение должно лечь на владельца «{name}».",
        )
        return [f"📎 Цена «{name}»: следующее обвинение — на предъявителя."]
    if cost == "GAIN_CONDITION":
        effect = str(item.get("cost_effect") or "").upper()
        if effect not in conditions.EFFECTS:
            effect = "NEXT_ROLL_DISADVANTAGE"
        notice = conditions._add(
            session,
            {
                "PLAYER": str(int(user_id)),
                "NAME": _clean(item.get("cost_name") or f"отдача от {name}", 80),
                "EFFECT": effect,
                "CLEAR": _clean(item.get("cost_clear") or "переждать или получить помощь", 180),
                "USES": "1" if effect == "NEXT_ROLL_DISADVANTAGE" else "0",
            },
        )
        return [notice] if notice else []
    return []


def _apply_mechanic(session, user_id: int, item, plan: dict) -> tuple[bool, list[str]]:
    from AI import dnd_conditions as conditions
    from AI import dnd_scene_clocks as clocks
    from AI import dnd_scene_tactics as tactics

    mechanic = _mechanic(item)
    name = inventory_fun._name(item)
    notices = []

    if mechanic in _ADVANTAGE_DOMAINS:
        session.item_boosts[str(int(user_id))] = {
            "domain": _ADVANTAGE_DOMAINS[mechanic],
            "source": name,
        }
        notices.append(f"⚙️ «{name}»: следующий подходящий бросок получает преимущество.")
    elif mechanic == "CLEAR_CONDITION":
        target = _condition_target(session, user_id, item)
        if target is None:
            return False, []
        notice = conditions._remove(
            session,
            {
                "PLAYER": str(int(user_id)),
                "NAME": str(target.get("name") or ""),
            },
        )
        if notice:
            notices.append(f"⚙️ «{name}»: {notice}")
    elif mechanic == "CREATE_EXIT":
        tactics._ensure(session)
        object_id = f"item-exit-{int(user_id)}-{int(getattr(session, 'scene_count', 0) or 0)}"
        tactics._upsert_object(
            session,
            "UPSERT",
            {
                "ID": object_id,
                "NAME": f"выход от «{name}»",
                "STATE": "физический проход открыт и доступен",
                "DETAIL": "этот выход создан активным предметом и существует в сцене как установленный факт",
                "AVAILABLE": "1",
            },
        )
        notices.append(f"🚪 «{name}»: в сцене гарантированно появляется физический выход.")
    elif mechanic in {"CLOCK_PUSH", "CLOCK_COMPLETE"}:
        row = _select_clock(session, item)
        if row is None:
            return False, []
        if mechanic == "CLOCK_PUSH":
            notice = clocks._delta_clock(
                session,
                {
                    "ID": str(row.get("id")),
                    "DELTA": str(_safe_int(item.get("power"), 1, 1, 3)),
                    "CAUSE": f"применён предмет «{name}»",
                },
            )
        else:
            notice = clocks._complete_clock(
                session,
                {
                    "ID": str(row.get("id")),
                    "CAUSE": f"прямое решение предметом «{name}»",
                },
            )
        if notice:
            notices.append(notice)
    elif mechanic == "CLEAR_ACCUSATION":
        notices.append(f"📄 «{name}»: текущее обвинение с предъявителя считается снятым.")
    else:
        return False, []

    return True, notices


def _commit_plan(session, user_id: int, plan: dict) -> list[str]:
    items = (getattr(session, "inventories", {}) or {}).get(str(int(user_id)), [])
    index = inventory_fun._find_item_index(items, str(plan.get("name") or ""))
    if index is None:
        return []
    item = items[index]
    if not _mechanic(item) or _charges_remaining(item) <= 0:
        return []

    success, notices = _apply_mechanic(session, user_id, item, plan)
    if not success:
        return []

    _spend_charge(item)
    remaining = _charges_remaining(item)
    maximum = _charges_max(item)
    notices.append(f"🔋 «{inventory_fun._name(item)}»: заряд {remaining}/{maximum}.")
    notices.extend(_apply_cost(session, user_id, item, plan))
    return notices


def commit_pending_item_uses(session) -> list[str]:
    _ensure(session)
    notices = []
    plans = list(session.pending_item_uses.items())
    for player, plan in plans:
        try:
            user_id = int(player)
        except (TypeError, ValueError):
            continue
        if isinstance(plan, dict):
            notices.extend(_commit_plan(session, user_id, plan))
    session.pending_item_uses = {}
    return [notice for notice in notices if notice]


def _apply_item_fields(item: dict, fields: dict) -> bool:
    mechanic = str(fields.get("MECH") or "").upper()
    if mechanic not in VALID_MECH:
        return False
    changed = False
    mapping = {
        "mechanic": mechanic,
        "requirement": str(fields.get("REQUIRE") or "NONE").upper(),
        "cost": str(fields.get("COST") or "NONE").upper(),
        "charges_max": _safe_int(fields.get("CHARGES"), 1, 1, 3),
        "power": _safe_int(fields.get("POWER"), 1, 1, 3),
        "clock_kind": str(fields.get("CLOCK_KIND") or "").upper(),
        "clock_id": _clean(fields.get("CLOCK_ID"), 64),
        "target_effect": str(fields.get("TARGET_EFFECT") or "").upper(),
        "cost_name": _clean(fields.get("COST_NAME"), 80),
        "cost_effect": str(fields.get("COST_EFFECT") or "").upper(),
        "cost_clear": _clean(fields.get("COST_CLEAR"), 180),
    }
    if mapping["requirement"] not in VALID_REQUIREMENTS:
        mapping["requirement"] = "NONE"
    if mapping["cost"] not in VALID_COSTS:
        mapping["cost"] = "NONE"
    for key, value in mapping.items():
        if value not in {"", None} and item.get(key) != value:
            item[key] = value
            changed = True
    if item.get("charges_remaining") != mapping["charges_max"]:
        item["charges_remaining"] = mapping["charges_max"]
        changed = True
    return changed


def apply_item_action_metadata(campaign, session, text, cleaned, notices):
    valid_players = _valid_players(session)
    for match in campaign.META_RE.finditer(str(text or "")):
        kind, payload = match.group(1).upper(), match.group(2)
        if kind != "ITEM":
            continue
        head, fields = campaign._parse_fields(payload)
        if str(head or "").upper() != "ADD" or not fields.get("MECH"):
            continue
        player = str(fields.get("PLAYER") or "")
        item_name = str(fields.get("NAME") or "").strip()
        if not player.isdigit() or not item_name or (valid_players and player not in valid_players):
            continue
        inventory = (getattr(session, "inventories", {}) or {}).get(player, [])
        index = inventory_fun._find_item_index(inventory, item_name)
        if index is None:
            continue
        current = inventory[index]
        if not isinstance(current, dict):
            current = {"name": inventory_fun._name(current), "kind": inventory_fun._kind(current)}
            inventory[index] = current
        if _apply_item_fields(current, fields):
            from AI import dnd_inventory_effects as effects
            effects._refresh_notice(notices, session, player, current)
    return cleaned, notices


def _parse_itemfact_fields(raw: str) -> tuple[str, dict[str, str]]:
    parts = [part.strip() for part in str(raw or "").split(";") if part.strip()]
    head = parts[0].upper() if parts else ""
    fields = {}
    for part in parts[1:]:
        key, sep, value = part.partition(":")
        if sep and value.strip():
            fields[key.strip().upper()] = value.strip()
    return head, fields


def apply_item_fact_metadata(session, original_text: str, cleaned: str, notices: list[str]):
    _ensure(session)
    for match in _ITEMFACT_RE.finditer(str(original_text or "")):
        head, fields = _parse_itemfact_fields(match.group(1))
        if head != "RESOLVE":
            continue
        player = str(fields.get("PLAYER") or "")
        kind = str(fields.get("KIND") or "").upper()
        if not player.isdigit() or not kind:
            continue
        session.item_world_facts = [
            fact for fact in session.item_world_facts
            if not (
                str(fact.get("kind") or "").upper() == kind
                and str(fact.get("player")) == player
            )
        ]
    return _ITEMFACT_RE.sub("", str(cleaned or "")).strip(), notices


def format_item_mechanic(item) -> str:
    mechanic = _mechanic(item)
    if not mechanic:
        return ""
    remaining, maximum = _charges_remaining(item), _charges_max(item)
    parts = [f"⚙️ {_MECH_LABELS[mechanic]}", f"заряд {remaining}/{maximum}"]
    requirement = _requirement(item)
    cost = _cost(item)
    if requirement in _REQUIREMENT_LABELS:
        parts.append(_REQUIREMENT_LABELS[requirement])
    if cost in _COST_LABELS:
        parts.append(_COST_LABELS[cost])
    return "; ".join(parts)


def _facts_context(session) -> str:
    _ensure(session)
    if not session.item_world_facts:
        return ""
    lines = []
    for fact in session.item_world_facts[-8:]:
        player = str(fact.get("player") or "")
        participant = (getattr(session, "participants", {}) or {}).get(player, {})
        who = participant.get("name") or f"ID {player}"
        lines.append(f"- {who}: {fact.get('text')}")
    return "\nНЕОПЛАЧЕННЫЕ/УСТАНОВЛЕННЫЕ ФАКТЫ АКТИВНЫХ ПРЕДМЕТОВ:\n" + "\n".join(lines)


def reset_adventure_charges(session, user_id: int) -> bool:
    changed = False
    items = (getattr(session, "inventories", {}) or {}).get(str(int(user_id)), [])
    for item in items:
        maximum = _charges_max(item)
        if maximum and isinstance(item, dict) and item.get("charges_remaining") != maximum:
            item["charges_remaining"] = maximum
            changed = True
    return changed


def _is_action_reply(dnd, event) -> bool:
    if dnd._is_group_action_reply(event):
        return True
    from AI.dnd_any_bot_reply import is_any_bot_action_reply
    return bool(is_any_bot_action_reply(event))


class DndItemActionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from AI import dnd

        chat = getattr(event, "chat", None)
        user = getattr(event, "from_user", None)
        if chat is None or user is None:
            return await handler(event, data)

        session = dnd.dnd_sessions.get(int(chat.id))
        if (
            not session
            or not dnd._is_participant_mode(session)
            or getattr(session, "state", None) != "WAITING_ACTION"
            or not _is_action_reply(dnd, event)
        ):
            return await handler(event, data)

        _ensure(session)
        user_id = int(user.id)
        plan = _parse_item_use(session, user_id, getattr(event, "text", None) or getattr(event, "caption", None) or "")
        if plan is not None:
            ok, error, plan = _prevalidate_plan(session, user_id, plan)
            if not ok:
                await event.answer(error or "Предмет сейчас не срабатывает.")
                return None

        result = await handler(event, data)

        current = dnd.dnd_sessions.get(int(chat.id))
        if not current or getattr(current, "state", None) != "WAITING_ACTION":
            return result
        _ensure(current)
        key = str(user_id)
        action_row = (getattr(current, "pending_actions", {}) or {}).get(key)
        if not isinstance(action_row, dict):
            return result

        if plan is None:
            current.pending_item_uses.pop(key, None)
        else:
            current.pending_item_uses[key] = deepcopy(plan)
            action_row["action"] = _append_item_action_note(action_row.get("action"), plan)
        dnd.persist_dnd_sessions()
        return result


def install_dnd_item_actions(dnd, dnd_router, *, state_policy, metadata_policy) -> None:
    from AI import dnd_campaign as campaign
    from AI import dnd_inventory_effects as effects

    if getattr(dnd, "_upupa_dnd_item_actions_installed", False):
        return

    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field("pending_item_uses", lambda session: dict(getattr(session, "pending_item_uses", {}) or {}))
    state_policy.add_state_field("item_boosts", lambda session: dict(getattr(session, "item_boosts", {}) or {}))
    state_policy.add_state_field("item_world_facts", lambda session: list(getattr(session, "item_world_facts", []) or []))
    state_policy.add_restore_hook(_restore)

    metadata_policy.add_postprocessor(
        lambda session, text, cleaned, notices: apply_item_action_metadata(
            campaign, session, text, cleaned, notices
        )
    )
    metadata_policy.add_postprocessor(apply_item_fact_metadata)

    if ITEM_ACTIONS_MARKER not in campaign.RULES:
        campaign.RULES = campaign.RULES.rstrip() + "\n" + ITEM_ACTION_RULES
    if ITEM_ACTIONS_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + ITEM_ACTION_RULES

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        return original_context(dnd_module, session) + "\n" + ITEM_ACTION_RULES + _facts_context(session)

    campaign._campaign_context = campaign_context

    original_format_effect = effects.format_inventory_effect

    def format_inventory_effect(item):
        base = original_format_effect(item)
        mechanic = format_item_mechanic(item)
        return "; ".join(part for part in (base, mechanic) if part)

    effects.format_inventory_effect = format_inventory_effect

    original_heritage = campaign._apply_heritage

    def apply_heritage(session, user_id, continuation=False):
        result = original_heritage(session, user_id, continuation=continuation)
        key = str(int(user_id))
        if key in (getattr(session, "inventories", {}) or {}):
            session.inventories[key] = [deepcopy(item) for item in session.inventories[key]]
        reset_adventure_charges(session, int(user_id))
        return result

    campaign._apply_heritage = apply_heritage

    original_generate = dnd.generate_session_response

    async def generate_session_response(session, prompt):
        _ensure(session)
        pending = bool(session.pending_item_uses)
        enriched_prompt = str(prompt or "")
        if pending and "Игроки заявили действия одновременно:" in enriched_prompt:
            enriched_prompt += _mechanics_prompt(session)
        result = await original_generate(session, enriched_prompt)
        if pending and "Игроки заявили действия одновременно:" in enriched_prompt:
            notices = commit_pending_item_uses(session)
            dnd.persist_dnd_sessions()
            if notices:
                result = "\n".join(notices) + "\n\n" + str(result or "")
        return result

    dnd.generate_session_response = generate_session_response

    original_open_action = dnd.open_action_window

    async def open_action_window(bot, chat_id, target_user_ids=None):
        session = dnd.dnd_sessions.get(chat_id)
        if session:
            _ensure(session)
            session.pending_item_uses = {}
        return await original_open_action(bot, chat_id, target_user_ids=target_user_ids)

    dnd.open_action_window = open_action_window

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        guarded = str(response or "")
        source = None
        if session and dnd._is_participant_mode(session):
            guarded, source = apply_item_boost(session, guarded)
            if source:
                dnd.persist_dnd_sessions()
        return await original_parse(bot, chat_id, guarded)

    dnd.parse_and_execute_turn = parse_turn

    dnd_router.message.outer_middleware(DndItemActionMiddleware())
    dnd._upupa_dnd_item_actions_installed = True


__all__ = [
    "ITEM_ACTIONS_MARKER",
    "ITEM_ACTION_RULES",
    "VALID_MECH",
    "VALID_REQUIREMENTS",
    "VALID_COSTS",
    "apply_item_action_metadata",
    "apply_item_boost",
    "commit_pending_item_uses",
    "format_item_mechanic",
    "reset_adventure_charges",
    "install_dnd_item_actions",
]
