"""Archetype abilities, Help and Dodge — table actions resolved by code.

Archetype abilities are bonus actions: they never spend the hero's turn and
never call the model. Their uses per adventure grow with level
(``dnd_progression.ability_charges_for_level``).

Prepared advantage reuses the existing ``item_boosts`` slot (one per hero),
which every roll path already consumes atomically; domain ``ANY`` matches the
next check, save or attack of that hero.
"""
from __future__ import annotations

import copy
import random

from AI import dnd_progression as progression

ABILITY_KEYS = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
_ARCHETYPE_BY_ABILITY = {"STR": "сила", "DEX": "ловкость", "CON": "стойкость",
                         "INT": "разум", "WIS": "мудрость", "CHA": "харизма"}

ABILITIES = {
    "сила": {"id": "MIGHT", "title": "Богатырский натиск", "emoji": "💪",
             "text": "следующая атака или силовой бросок с преимуществом", "effect": "boost",
             "domain": "COMBAT", "target": "self"},
    "ловкость": {"id": "MANEUVER", "title": "Ловкий манёвр", "emoji": "🤸",
                 "text": "следующий бросок движения, скрытности или уклонения с преимуществом", "effect": "boost",
                 "domain": "MOVE", "target": "self"},
    "разум": {"id": "INSIGHT", "title": "Озарение", "emoji": "🧠",
              "text": "следующий бросок наблюдения или анализа с преимуществом", "effect": "boost",
              "domain": "PERCEPTION", "target": "self"},
    "харизма": {"id": "INSPIRE", "title": "Вдохновение", "emoji": "🎶",
                "text": "союзник получает преимущество на следующий любой бросок", "effect": "boost",
                "domain": "ANY", "target": "ally"},
    "мудрость": {"id": "HEALING_WORD", "title": "Целительное слово", "emoji": "🌿",
                 "text": "восстанавливает 1d8 + мод. МДР HP себе или союзнику", "effect": "heal",
                 "dice": 8, "bonus": "WIS", "target": "any"},
    "стойкость": {"id": "SECOND_WIND", "title": "Второе дыхание", "emoji": "🫁",
                  "text": "восстанавливает себе 1d10 + уровень HP", "effect": "heal",
                  "dice": 10, "bonus": "LEVEL", "target": "self"},
}
DOMAIN_LABELS = {"MOVE": "движение", "SOCIAL": "общение", "PERCEPTION": "наблюдение",
                 "COMBAT": "бой", "ANY": "любой следующий бросок"}


class AbilityError(ValueError):
    pass


def ensure(session) -> None:
    if not isinstance(getattr(session, "archetype_ability_uses", None), dict):
        session.archetype_ability_uses = {}
    if not isinstance(getattr(session, "dodge_flags", None), dict):
        session.dodge_flags = {}
    if not isinstance(getattr(session, "item_boosts", None), dict):
        session.item_boosts = {}


def _sheet(session, user_id):
    return (getattr(session, "character_sheets", {}) or {}).get(str(int(user_id)))


def _alive(sheet) -> bool:
    if not isinstance(sheet, dict):
        return False
    try:
        hp = int(sheet.get("hp", 0))
    except (TypeError, ValueError):
        return False
    return hp > 0 and str(sheet.get("status") or "alive") != "dead"


def _name(session, user_id) -> str:
    row = (getattr(session, "participants", {}) or {}).get(str(int(user_id))) or {}
    return row.get("name") or f"Герой {user_id}"


def archetype_of(session, user_id) -> str | None:
    profile = (getattr(session, "character_profiles", {}) or {}).get(str(int(user_id))) or {}
    chosen = str(profile.get("archetype") or "").casefold()
    if chosen in ABILITIES:
        return chosen
    sheet = _sheet(session, user_id) or {}
    base = sheet.get("base_stats") if isinstance(sheet.get("base_stats"), dict) else sheet.get("stats")
    if not isinstance(base, dict) or not all(key in base for key in ABILITY_KEYS):
        return None
    best = max(ABILITY_KEYS, key=lambda key: (int(base.get(key, 0) or 0), -ABILITY_KEYS.index(key)))
    return _ARCHETYPE_BY_ABILITY[best]


def ability_for(session, user_id) -> dict | None:
    archetype = archetype_of(session, user_id)
    return dict(ABILITIES[archetype], archetype=archetype) if archetype else None


def charges(session, user_id) -> tuple[int, int]:
    ensure(session)
    maximum = progression.ability_charges_for_level(progression.level_of(session, user_id))
    used = int(session.archetype_ability_uses.get(str(int(user_id)), 0) or 0)
    return max(0, maximum - used), maximum


def allies(session, user_id) -> list[int]:
    result = []
    for key, row in (getattr(session, "participants", {}) or {}).items():
        if not isinstance(row, dict) or row.get("active", True) is False:
            continue
        try:
            uid = int(row.get("user_id", key))
        except (TypeError, ValueError):
            continue
        if uid != int(user_id) and _alive(_sheet(session, uid)):
            result.append(uid)
    return result


def valid_targets(session, user_id, ability) -> list[int]:
    kind = ability.get("target")
    if kind == "self":
        return [int(user_id)]
    if kind == "ally":
        return allies(session, user_id)
    return [int(user_id)] + allies(session, user_id)


def _modifier(session, user_id, ability) -> int:
    stats = (_sheet(session, user_id) or {}).get("stats") or {}
    return (int(stats.get(ability, 10) or 10) - 10) // 2


def use_ability(session, user_id, target_id=None, *, rng=None) -> str:
    """Apply the hero's archetype ability. Raises AbilityError when invalid."""
    ensure(session)
    rng = rng or random
    actor = int(user_id)
    ability = ability_for(session, actor)
    if ability is None:
        raise AbilityError("У героя пока нет умения: сначала нужны характеристики.")
    if not _alive(_sheet(session, actor)):
        raise AbilityError("Герой без сознания и не может применить умение.")
    remaining, _maximum = charges(session, actor)
    if remaining <= 0:
        raise AbilityError(f"«{ability['title']}» уже исчерпано в этом приключении.")
    target = actor if target_id in (None, "", 0) else int(target_id)
    if target not in valid_targets(session, actor, ability):
        raise AbilityError("Эта цель недоступна для умения.")

    if ability["effect"] == "boost":
        key = str(target)
        if isinstance(session.item_boosts.get(key), dict):
            raise AbilityError(f"У {_name(session, target)} уже есть подготовленное преимущество.")
        session.item_boosts[key] = {"domain": ability["domain"], "source": ability["title"]}
        text = f"{ability['emoji']} {_name(session, actor)}: «{ability['title']}»"
        text += f" → {_name(session, target)}" if target != actor else ""
        text += f". Преимущество: {DOMAIN_LABELS[ability['domain']]}."
    else:
        sheet = _sheet(session, target)
        if not _alive(sheet):
            raise AbilityError("Лечить можно только героя в сознании.")
        roll = rng.randint(1, int(ability["dice"]))
        bonus = progression.level_of(session, actor) if ability["bonus"] == "LEVEL" else _modifier(session, actor, ability["bonus"])
        amount = max(1, roll + bonus)
        old_hp, max_hp = int(sheet["hp"]), int(sheet.get("max_hp") or sheet["hp"])
        if old_hp >= max_hp:
            raise AbilityError(f"{_name(session, target)} и так полностью здоров.")
        sheet["hp"] = min(max_hp, old_hp + amount)
        text = (f"{ability['emoji']} {_name(session, actor)}: «{ability['title']}»"
                + (f" → {_name(session, target)}" if target != actor else "")
                + f". 🎲 {roll}{bonus:+d} = {amount}; ❤️ {old_hp} → {sheet['hp']}/{max_hp}.")
    session.archetype_ability_uses[str(actor)] = int(session.archetype_ability_uses.get(str(actor), 0) or 0) + 1
    remaining, maximum = charges(session, actor)
    return text + f" Осталось {remaining}/{maximum}."


def prepare_help(session, helper_id, ally_id) -> str:
    """Help action: the helper spends their turn, the ally's next roll gets advantage once."""
    ensure(session)
    helper, ally = int(helper_id), int(ally_id)
    if ally not in allies(session, helper):
        raise AbilityError("Помочь можно живому союзнику в сцене.")
    if isinstance(session.item_boosts.get(str(ally)), dict):
        raise AbilityError(f"У {_name(session, ally)} уже есть подготовленное преимущество.")
    session.item_boosts[str(ally)] = {"domain": "ANY", "source": f"помощь: {_name(session, helper)}"}
    return f"🤝 {_name(session, helper)} помогает {_name(session, ally)}: следующий бросок с преимуществом."


def prepare_dodge(session, user_id) -> str:
    ensure(session)
    session.dodge_flags[str(int(user_id))] = True
    return f"🛡 {_name(session, user_id)} уходит в оборону: следующая атака по нему с помехой."


def consume_dodge(session, target_id) -> bool:
    flags = getattr(session, "dodge_flags", None)
    if not isinstance(flags, dict):
        return False
    return bool(flags.pop(str(int(target_id)), None))


MENU_ACTIONS_RULES = (
    "ЗАЯВКИ ИЗ МЕНЮ: [Навык: X (характеристика)] — выбранный игроком способ; если исход неопределён, "
    "ставь ROLL с этим SKILL и ABILITY. [Помощь: имя] и [Оборона] уже применены кодом (преимущество "
    "союзнику / помеха следующей атаке врага) — не дублируй MODE. [Предмет: …], [Объект: …] — чем действует герой."
)


def install_dnd_archetype_abilities(*, state_policy) -> None:
    from AI.dnd_prompt_registry import PromptSection, is_participant_session, register_prompt_section

    register_prompt_section(PromptSection("menu_actions", MENU_ACTIONS_RULES, order=65, when=is_participant_session))
    if getattr(state_policy, "_upupa_archetype_abilities", False):
        return
    state_policy.add_ensure_hook(ensure)
    for field in ("archetype_ability_uses", "dodge_flags"):
        state_policy.add_state_field(field, lambda session, name=field: copy.deepcopy(getattr(session, name, {}) or {}))
    state_policy._upupa_archetype_abilities = True


__all__ = [
    "ABILITIES", "AbilityError", "DOMAIN_LABELS", "ability_for", "allies", "archetype_of", "charges",
    "consume_dodge", "install_dnd_archetype_abilities", "prepare_dodge", "prepare_help", "use_ability",
    "valid_targets",
]
