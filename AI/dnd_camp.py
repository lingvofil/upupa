"""Short rest («привал»): a calm-moment turn with a concrete, local benefit.

Between fights the party had nothing to do with a quiet moment except narrate
it. A hero may now spend their turn at camp; the mechanical part is applied by
code (no AI), then the turn goes to the master as an ordinary declaration with
the ``[Привал]`` prefix so the scene reflects it:

  🩹 Перевязать раны      — 1d6 + модификатор телосложения HP (не выше максимума)
  🔄 Восстановить силы    — вернуть один израсходованный заряд умения роли
  🛠 Подготовиться         — преимущество на следующий бросок (общий слот подготовки)
  🔥 Байка у костра        — свободный рассказ; мастер вплетает его в сцену

Available only in a free turn with no living enemies, once per hero every
``CAMP_COOLDOWN_SCENES`` scenes.
"""
from __future__ import annotations

import copy
import random

CAMP_COOLDOWN_SCENES = 3
ACTIVITIES = {
    "rest": {"emoji": "🩹", "title": "Перевязать раны"},
    "restore": {"emoji": "🔄", "title": "Восстановить силы"},
    "prepare": {"emoji": "🛠", "title": "Подготовиться"},
    "story": {"emoji": "🔥", "title": "Байка у костра"},
}


class CampError(ValueError):
    pass


def ensure(session) -> None:
    if not isinstance(getattr(session, "camp_last_scene", None), dict):
        session.camp_last_scene = {}


def _scene(session) -> int:
    return int(getattr(session, "scene_count", 0) or 0)


def _sheet(session, user_id) -> dict:
    row = (getattr(session, "character_sheets", {}) or {}).get(str(int(user_id)))
    return row if isinstance(row, dict) else {}


def _alive_enemies(session) -> bool:
    rules = getattr(session, "local_enemy_rules", {}) or {}
    for key, row in (getattr(session, "enemy_combatants", {}) or {}).items():
        if not isinstance(row, dict) or (rules.get(key) or {}).get("retreated"):
            continue
        try:
            hp = int(row.get("hp", 1))
        except (TypeError, ValueError):
            hp = 1
        if row.get("status") != "dead" and hp > 0:
            return True
    return False


def scenes_until_camp(session, user_id) -> int:
    last = (getattr(session, "camp_last_scene", None) or {}).get(str(int(user_id)))
    if last is None:
        return 0
    return max(0, int(last) + CAMP_COOLDOWN_SCENES - _scene(session))


def camp_possible(session) -> bool:
    """The scene allows a rest at all (shared card)."""
    return getattr(session, "state", None) == "WAITING_ACTION" and not _alive_enemies(session)


def can_camp(session, user_id) -> bool:
    sheet = _sheet(session, user_id)
    alive = sheet.get("status") != "dead" and int(sheet.get("hp", 1) or 0) > 0 if sheet else True
    return camp_possible(session) and alive and scenes_until_camp(session, user_id) == 0


def options(session, user_id) -> dict[str, str]:
    """activity -> why it is unavailable ('' when available)."""
    from AI.dnd_archetype_abilities import ability_for, charges

    sheet = _sheet(session, user_id)
    result = {key: "" for key in ACTIVITIES}
    if sheet and int(sheet.get("hp", 0) or 0) >= int(sheet.get("max_hp", 0) or 0):
        result["rest"] = "здоров"
    ability = ability_for(session, user_id)
    if not ability:
        result["restore"] = "нет умения"
    else:
        remaining, maximum = charges(session, user_id)
        if remaining >= maximum:
            result["restore"] = "заряды полны"
    if isinstance((getattr(session, "item_boosts", None) or {}).get(str(int(user_id))), dict):
        result["prepare"] = "уже готов"
    return result


def _name(session, user_id) -> str:
    row = (getattr(session, "participants", {}) or {}).get(str(int(user_id))) or {}
    return row.get("name") or f"игрок {user_id}"


def apply_camp(session, user_id, activity: str, *, rng=None) -> tuple[str, str]:
    """Apply the camp benefit; returns (public notice, declaration for the master)."""
    rng = rng or random
    ensure(session)
    if activity not in ACTIVITIES:
        raise CampError("Неизвестное занятие на привале.")
    if not can_camp(session, user_id):
        wait = scenes_until_camp(session, user_id)
        raise CampError("Привал сейчас невозможен." if not wait else f"Следующий привал через сцен: {wait}.")
    blocked = options(session, user_id).get(activity)
    if blocked:
        raise CampError(f"«{ACTIVITIES[activity]['title']}» сейчас без толку: {blocked}.")
    name = _name(session, user_id)
    if activity == "rest":
        sheet = _sheet(session, user_id)
        con = int((sheet.get("stats") or {}).get("CON", 10) or 10)
        healed = max(1, rng.randint(1, 6) + (con - 10) // 2)
        before = int(sheet.get("hp", 0) or 0)
        sheet["hp"] = min(int(sheet.get("max_hp", before) or before), before + healed)
        gained = sheet["hp"] - before
        notice = f"🩹 {name} перевязывает раны: +{gained} HP (❤️ {sheet['hp']}/{sheet['max_hp']})."
        declaration = f"[Привал] {name} перевязывает раны у костра (+{gained} HP, уже применено кодом)."
    elif activity == "restore":
        uses = session.archetype_ability_uses
        uses[str(int(user_id))] = max(0, int(uses.get(str(int(user_id)), 0) or 0) - 1)
        from AI.dnd_archetype_abilities import charges

        remaining, maximum = charges(session, user_id)
        notice = f"🔄 {name} переводит дух: заряд умения восстановлен ({remaining}/{maximum})."
        declaration = f"[Привал] {name} отдыхает и собирается с силами (заряд умения восстановлен кодом)."
    elif activity == "prepare":
        session.item_boosts[str(int(user_id))] = {"domain": "ANY", "source": "подготовка на привале"}
        notice = f"🛠 {name} готовится к следующему делу: преимущество на следующий бросок."
        declaration = f"[Привал] {name} готовит снаряжение и план (преимущество уже подготовлено кодом)."
    else:
        notice = ""
        declaration = ""
    session.camp_last_scene[str(int(user_id))] = _scene(session)
    return notice, declaration


CAMP_RULES = ("[Привал] — герой отдыхает; польза уже применена кодом, не дублируй её. Опиши короткий спокойный момент; "
              "если в сцене тикает опасность, время работает против партии.")


def install_dnd_camp(*, state_policy) -> None:
    from AI.dnd_prompt_registry import PromptSection, is_participant_session, register_prompt_section

    register_prompt_section(PromptSection(
        "camp", CAMP_RULES, order=66, optional=True,
        # Taught only when a rest is possible at all: never in a fight.
        when=lambda session, prompt: is_participant_session(session, prompt) and not _alive_enemies(session)))
    if getattr(state_policy, "_upupa_dnd_camp", False):
        return
    state_policy.add_ensure_hook(ensure)
    state_policy.add_state_field("camp_last_scene", lambda s: copy.deepcopy(getattr(s, "camp_last_scene", None) or {}))
    state_policy._upupa_dnd_camp = True


__all__ = ["ACTIVITIES", "CampError", "apply_camp", "camp_possible", "can_camp", "install_dnd_camp", "options",
           "scenes_until_camp"]
