"""Scale player-controlled emergency heals with DnD party size."""
from __future__ import annotations

import random


SCALED_HEAL_RULE = (
    "При падении героя до 0 HP он сначала оказывается при смерти и не участвует в ходах. "
    "Количество одноразовых аварийных лечилок задаёт код по размеру партии: одна лечилка на каждые три героя "
    "с округлением вверх (1–3 героя — 1; 4–6 — 2; 7–9 — 3 и так далее). "
    "Лечилки заранее случайно распределяются между разными героями, пока это возможно. "
    "Когда герой при смерти, код по очереди спрашивает владельцев доступных лечилок, тратить ли одну на него. "
    "Согласие расходует только выбранную лечилку и возвращает героя в игру. Отказ лечить другого героя не расходует лечилку, "
    "но для этого конкретного падения очередь переходит к следующей доступной лечилке. "
    "Если владелец отказался спасать самого себя, его лечилка теряется; другие доступные лечилки всё ещё могут его спасти. "
    "Точное текущее количество и владельцы указаны в боевом контексте: не создавай дополнительные лечилки "
    "и не лечи HP повествовательно без явной игровой причины."
)

_INIT_SCALED_TEXT = (
    "Когда кто-то свалится до 0 HP, бот спросит владельца одной из доступных лечилок, тратить ли её. "
    "Если он откажется, при наличии других лечилок решение перейдёт к следующему владельцу."
)

_SECOND_HEAL_MESSAGE_MARKER = "Вторая одноразовая лечилка случайно досталась"


def _healing_charge_count(party_size: int) -> int:
    try:
        count = max(0, int(party_size))
    except (TypeError, ValueError):
        return 0
    return (count + 2) // 3 if count else 0


def _next_owner(user_ids: list[int], occupied: set[int]) -> int | None:
    if not user_ids:
        return None
    available = [user_id for user_id in user_ids if user_id not in occupied]
    return random.choice(available or user_ids)


def _ensure_scaled_charges(two_heals, session) -> list[dict]:
    user_ids = two_heals._participant_ids(session)
    sheets = getattr(session, "character_sheets", {}) or {}
    if not user_ids or not sheets:
        session.healing_charges = []
        if not user_ids:
            session.healing_charge = {"owner_id": None, "used": True}
        return session.healing_charges

    desired = _healing_charge_count(len(user_ids))
    charges = []
    raw = getattr(session, "healing_charges", None)
    if isinstance(raw, list):
        for index, item in enumerate(raw, start=1):
            normalized = two_heals._normalized_charge(item, index)
            if normalized and int(normalized["owner_id"]) in user_ids:
                normalized["id"] = len(charges) + 1
                charges.append(normalized)
            if len(charges) >= desired:
                break

    if not charges:
        legacy = two_heals._normalized_charge(getattr(session, "healing_charge", None), 1)
        if legacy and int(legacy["owner_id"]) in user_ids:
            legacy["id"] = 1
            charges.append(legacy)

    if not charges and desired:
        owner_id = random.choice(user_ids)
        charges.append({"id": 1, "owner_id": owner_id, "used": False, "lost": False})

    occupied = {int(charge["owner_id"]) for charge in charges if charge.get("owner_id") is not None}
    while len(charges) < desired:
        owner_id = _next_owner(user_ids, occupied)
        if owner_id is None:
            break
        charges.append({
            "id": len(charges) + 1,
            "owner_id": int(owner_id),
            "used": False,
            "lost": False,
        })
        occupied.add(int(owner_id))

    session.healing_charges = charges[:desired]
    for index, charge in enumerate(session.healing_charges, start=1):
        charge["id"] = index
    two_heals._sync_legacy_charge(session)
    return session.healing_charges


def _generalize_decision_copy(text: str, prompt: str) -> tuple[str, str]:
    text = str(text).replace(
        "Но есть вторая: теперь решает",
        "Но есть ещё лечилка: теперь решает",
    ).replace(
        "со второй лечилкой",
        "со следующей лечилкой",
    )
    prompt = str(prompt).replace(
        "одну из двух аварийных лечилок",
        "одну из аварийных лечилок",
    )
    return text, prompt


class _InitBotProxy:
    def __init__(self, bot):
        self._bot = bot

    async def send_message(self, chat_id, text, *args, **kwargs):
        if _SECOND_HEAL_MESSAGE_MARKER in str(text):
            return None
        return await self._bot.send_message(chat_id, text, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._bot, name)


def install_dnd_scaled_heals(dnd_router) -> None:
    """Generalize the historical two-heal layer to a party-size quota."""
    from AI import dnd
    from AI import dnd_combat as combat
    from AI import dnd_healing_choice as healing_choice
    from AI import dnd_two_heals as two_heals

    if getattr(dnd_router, "_upupa_dnd_scaled_heals_configured", False):
        return

    old_rule = two_heals.TWO_HEAL_RULE
    if old_rule in combat.COMBAT_RULES:
        combat.COMBAT_RULES = combat.COMBAT_RULES.replace(old_rule, SCALED_HEAL_RULE)
    elif SCALED_HEAL_RULE not in combat.COMBAT_RULES:
        combat.COMBAT_RULES = combat.COMBAT_RULES.rstrip() + "\n" + SCALED_HEAL_RULE
    if old_rule in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.replace(old_rule, SCALED_HEAL_RULE)
    elif SCALED_HEAL_RULE not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + SCALED_HEAL_RULE
    healing_choice._INIT_CHOICE_TEXT = _INIT_SCALED_TEXT

    # Functions installed by dnd_two_heals resolve this module global dynamically,
    # so replacing it upgrades persistence, status output and decline chains too.
    def ensure_charges(session):
        return _ensure_scaled_charges(two_heals, session)

    two_heals._ensure_charges = ensure_charges

    original_apply = healing_choice._apply_heal_decision

    def apply_heal_decision(combat_module, session, *, use_heal: bool):
        text, prompt, all_dead = original_apply(combat_module, session, use_heal=use_heal)
        text, prompt = _generalize_decision_copy(text, prompt)
        return text, prompt, all_dead

    healing_choice._apply_heal_decision = apply_heal_decision

    # dnd_two_heals has its own initializer that always creates charge #2 and
    # announces it. Let it preserve legacy initialization, suppress that obsolete
    # announcement, then resize the actual charge list to the new quota.
    original_initialize = combat.initialize_party_combat

    async def initialize_party_combat(dnd_module, bot, session):
        result = await original_initialize(dnd_module, _InitBotProxy(bot), session)
        charges = _ensure_scaled_charges(two_heals, session)
        dnd_module.persist_dnd_sessions()
        if len(charges) > 1:
            additional_names = [
                combat._participant_name(session, int(charge["owner_id"]))
                for charge in charges[1:]
            ]
            if len(additional_names) == 1:
                message = (
                    f"🧪 Дополнительная аварийная лечилка досталась {additional_names[0]}. "
                    f"Всего лечилок у партии: {len(charges)}."
                )
            else:
                message = (
                    f"🧪 Дополнительные аварийные лечилки достались: {', '.join(additional_names)}. "
                    f"Всего лечилок у партии: {len(charges)}."
                )
            await bot.send_message(session.chat_id, message)
        return result

    combat.initialize_party_combat = initialize_party_combat
    dnd_router._upupa_dnd_scaled_heals_configured = True
