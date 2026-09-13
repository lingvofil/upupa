"""Two independent player-controlled emergency heals for participant DnD."""
from __future__ import annotations

import logging
import random
import re


TWO_HEAL_RULE = (
    "При падении героя до 0 HP он сначала оказывается при смерти и не участвует в ходах. "
    "В партии ровно ДВЕ одноразовые аварийные лечилки, заранее случайно распределённые между героями; "
    "если героев хотя бы двое, лечилки по возможности получают разные владельцы. "
    "Когда герой при смерти, код по очереди спрашивает владельцев доступных лечилок, тратить ли одну на него. "
    "Согласие расходует только выбранную лечилку и возвращает героя в игру. Отказ лечить другого героя не расходует лечилку, "
    "но для этого конкретного падения очередь переходит к другой доступной лечилке. "
    "Если владелец отказался спасать самого себя, его лечилка теряется; другая доступная лечилка всё ещё может его спасти. "
    "Не создавай третью лечилку и не лечи HP повествовательно без явной игровой причины."
)


def _participant_ids(session) -> list[int]:
    result = []
    for item in (getattr(session, "participants", {}) or {}).values():
        try:
            result.append(int(item["user_id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _normalized_charge(raw, charge_id: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    owner = raw.get("owner_id")
    try:
        owner_id = int(owner) if owner is not None else None
    except (TypeError, ValueError):
        owner_id = None
    if owner_id is None:
        return None
    return {
        "id": int(raw.get("id") or charge_id),
        "owner_id": owner_id,
        "used": bool(raw.get("used")),
        "lost": bool(raw.get("lost")),
    }


def _second_owner(user_ids: list[int], first_owner: int | None) -> int | None:
    if not user_ids:
        return None
    alternatives = [user_id for user_id in user_ids if user_id != first_owner]
    return random.choice(alternatives or user_ids)


def _sync_legacy_charge(session) -> None:
    charges = getattr(session, "healing_charges", None) or []
    first = charges[0] if charges else None
    if first:
        session.healing_charge = {
            "owner_id": first.get("owner_id"),
            "used": bool(first.get("used")),
            "lost": bool(first.get("lost")),
        }
    elif not isinstance(getattr(session, "healing_charge", None), dict):
        session.healing_charge = {"owner_id": None, "used": True}


def _ensure_charges(session) -> list[dict]:
    raw = getattr(session, "healing_charges", None)
    if isinstance(raw, list) and raw:
        charges = []
        for index, item in enumerate(raw[:2], start=1):
            normalized = _normalized_charge(item, index)
            if normalized:
                charges.append(normalized)
        if charges:
            session.healing_charges = charges
            _sync_legacy_charge(session)
            return charges

    sheets = getattr(session, "character_sheets", {}) or {}
    user_ids = _participant_ids(session)
    if not sheets or not user_ids:
        session.healing_charges = []
        return session.healing_charges

    legacy = _normalized_charge(getattr(session, "healing_charge", None), 1)
    if legacy is None:
        first_owner = random.choice(user_ids)
        legacy = {"id": 1, "owner_id": first_owner, "used": False, "lost": False}
    else:
        legacy["id"] = 1
    second_owner = _second_owner(user_ids, legacy.get("owner_id"))
    charges = [legacy]
    if second_owner is not None:
        charges.append({"id": 2, "owner_id": second_owner, "used": False, "lost": False})
    session.healing_charges = charges
    _sync_legacy_charge(session)
    return charges


def _owner_can_use(session, owner_id: int, target_id: int | None) -> bool:
    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(int(owner_id)))
    if not isinstance(sheet, dict):
        return True
    status = str(sheet.get("status") or "alive").casefold()
    try:
        hp = int(sheet.get("hp", 0))
    except (TypeError, ValueError):
        hp = 0
    if target_id is not None and int(owner_id) == int(target_id) and status in {"dead", "dying"}:
        return True
    return status != "dead" and hp > 0


def _available_charge(session, *, target_id: int | None = None, excluded_ids=()) -> dict | None:
    excluded = {int(value) for value in excluded_ids}
    for charge in _ensure_charges(session):
        if int(charge.get("id", 0)) in excluded or charge.get("used") or charge.get("lost"):
            continue
        owner_id = charge.get("owner_id")
        if owner_id is None or not _owner_can_use(session, int(owner_id), target_id):
            continue
        return charge
    return None


def _available_count(session, *, target_id: int | None = None) -> int:
    count = 0
    for charge in _ensure_charges(session):
        if charge.get("used") or charge.get("lost") or charge.get("owner_id") is None:
            continue
        if _owner_can_use(session, int(charge["owner_id"]), target_id):
            count += 1
    return count


def _charge_by_id(session, charge_id: int) -> dict | None:
    for charge in _ensure_charges(session):
        if int(charge.get("id", 0)) == int(charge_id):
            return charge
    return None


def _heal_available(session) -> bool:
    return _available_charge(session) is not None


def _resolve_enemy_attack_with_choice(combat, session, attack: dict):
    living = sorted(combat._living_ids(session))
    if not living:
        summary, prompt, all_dead = combat._resolve_enemy_attack(session, attack)
        return summary, prompt, all_dead, False

    attack = dict(attack)
    target_id = attack.get("target_user_id")
    if target_id not in living:
        target_id = combat.random.choice(living)
        attack["target_user_id"] = target_id

    # Force the lower combat layer to resolve raw damage without auto-consuming
    # its legacy single charge. The two-charge choice is handled below.
    old_legacy = getattr(session, "healing_charge", None)
    session.healing_charge = {"owner_id": None, "used": True}
    try:
        summary, prompt, all_dead = combat._resolve_enemy_attack(session, attack)
    finally:
        session.healing_charge = old_legacy if isinstance(old_legacy, dict) else {"owner_id": None, "used": True}
        _sync_legacy_charge(session)

    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(int(target_id)))
    lethal = isinstance(sheet, dict) and int(sheet.get("hp", 0)) <= 0 and str(sheet.get("status")) == "dead"
    charge = _available_charge(session, target_id=int(target_id))
    if not (lethal and charge):
        return summary, prompt, all_dead, False

    sheet["hp"] = 0
    sheet["status"] = "dying"
    nonce = random.randint(100000, 999999)
    owner_id = int(charge["owner_id"])
    session.pending_heal_decision = {
        "target_id": int(target_id),
        "owner_id": owner_id,
        "charge_id": int(charge["id"]),
        "declined_charge_ids": [],
        "nonce": nonce,
        "attack_prompt": str(prompt),
        "reason": str(attack.get("reason") or "враг атакует"),
    }

    lines = [line for line in str(summary).splitlines() if not line.startswith("☠️ ")]
    lines.append(
        f"💀 {combat._participant_name(session, target_id)} при смерти на 0 HP. "
        f"Теперь решает {combat._participant_name(session, owner_id)} с лечилкой."
    )
    return "\n".join(lines), prompt, False, True


def _kill_after_declines(combat, session, pending, sheet, *, note: str):
    target_id = int(pending["target_id"])
    target_name = combat._participant_name(session, target_id)
    sheet["hp"] = 0
    sheet["status"] = "dead"
    session.pending_heal_decision = None
    all_dead = not combat._living_ids(session)
    result_text = f"☠️ {note} {target_name} погиб."
    correction = f"{target_name} погиб после решений владельцев лечилок."
    prompt = (str(pending.get("attack_prompt") or "") + " " + correction).strip()
    if all_dead:
        prompt += " Вся партия погибла: дай короткий финал и закончи [ACTION:END]."
    else:
        prompt += " Продолжай сюжет с этим точным решением игроков."
    return result_text, prompt, all_dead


def _apply_heal_decision(combat, session, *, use_heal: bool):
    pending = dict(getattr(session, "pending_heal_decision", None) or {})
    target_id = int(pending["target_id"])
    owner_id = int(pending["owner_id"])
    target_name = combat._participant_name(session, target_id)
    owner_name = combat._participant_name(session, owner_id)
    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(target_id))
    if not isinstance(sheet, dict):
        session.pending_heal_decision = None
        return "Решение протухло: героя уже нет в боевом состоянии.", "Продолжай сюжет.", False

    charge = _charge_by_id(session, int(pending.get("charge_id") or 0))
    if not charge or charge.get("used") or charge.get("lost"):
        next_charge = _available_charge(
            session,
            target_id=target_id,
            excluded_ids=pending.get("declined_charge_ids") or [],
        )
        if not next_charge:
            return _kill_after_declines(combat, session, pending, sheet, note="Лечилки кончились или потерялись.")
        charge = next_charge
        owner_id = int(charge["owner_id"])
        owner_name = combat._participant_name(session, owner_id)

    if use_heal:
        heal = combat.random.randint(1, 8) + 2
        restored = max(1, min(int(sheet.get("max_hp", heal)), heal))
        sheet["hp"] = restored
        sheet["status"] = "alive"
        charge["used"] = True
        session.pending_heal_decision = None
        _sync_legacy_charge(session)
        result_text = (
            f"🧪 {owner_name} тратит свою аварийную лечилку: "
            f"{target_name} возвращается с {restored} HP. Эта лечилка израсходована."
        )
        prompt = (
            str(pending.get("attack_prompt") or "")
            + f" ВАЖНО: {owner_name} потратил одну из двух аварийных лечилок на {target_name}; "
              f"{target_name} снова жив с {restored} HP. Продолжай сюжет с этим точным решением игрока."
        ).strip()
        return result_text, prompt, False

    declined = [int(value) for value in (pending.get("declined_charge_ids") or [])]
    if int(charge["id"]) not in declined:
        declined.append(int(charge["id"]))

    self_refusal = target_id == owner_id
    if self_refusal:
        charge["used"] = True
        charge["lost"] = True
        _sync_legacy_charge(session)

    next_charge = _available_charge(session, target_id=target_id, excluded_ids=declined)
    if next_charge:
        next_owner = int(next_charge["owner_id"])
        session.pending_heal_decision = {
            **pending,
            "owner_id": next_owner,
            "charge_id": int(next_charge["id"]),
            "declined_charge_ids": declined,
            "nonce": random.randint(100000, 999999),
        }
        if self_refusal:
            text = (
                f"☠️ {owner_name} отказался тратить свою лечилку на себя — она потеряна. "
                f"Но есть вторая: теперь решает {combat._participant_name(session, next_owner)}."
            )
        else:
            text = (
                f"🧪 {owner_name} решил приберечь свою лечилку и не спасать {target_name}. "
                f"Теперь решает {combat._participant_name(session, next_owner)} со второй лечилкой."
            )
        return text, str(pending.get("attack_prompt") or ""), False

    if self_refusal:
        note = f"{owner_name} отказался спасать себя, и его лечилка пропала. Другой помощи не осталось."
    else:
        note = f"{owner_name} не стал тратить лечилку, и другой доступной помощи не осталось."
    return _kill_after_declines(combat, session, pending, sheet, note=note)


def _charges_text(combat, session) -> str:
    chunks = []
    for charge in _ensure_charges(session):
        if charge.get("lost"):
            status = "потеряна"
        elif charge.get("used"):
            status = "использована"
        else:
            owner_id = int(charge["owner_id"])
            status = f"у ID {owner_id} ({combat._participant_name(session, owner_id)})"
        chunks.append(f"#{charge['id']} {status}")
    return "; ".join(chunks) or "нет"


def install_dnd_two_heals(dnd_router) -> None:
    from AI import dnd
    from AI import dnd_campaign as campaign
    from AI import dnd_combat as combat
    from AI import dnd_healing_choice as healing_choice

    if getattr(dnd_router, "_upupa_dnd_two_heals_configured", False):
        return

    # Remove the now-wrong one-charge rule from both prompt sources.
    if healing_choice._CHOICE_HEAL_RULE in combat.COMBAT_RULES:
        combat.COMBAT_RULES = combat.COMBAT_RULES.replace(healing_choice._CHOICE_HEAL_RULE, TWO_HEAL_RULE)
    elif TWO_HEAL_RULE not in combat.COMBAT_RULES:
        combat.COMBAT_RULES = combat.COMBAT_RULES.rstrip() + "\n" + TWO_HEAL_RULE
    if healing_choice._CHOICE_HEAL_RULE in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.replace(healing_choice._CHOICE_HEAL_RULE, TWO_HEAL_RULE)
    elif TWO_HEAL_RULE not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + TWO_HEAL_RULE
    healing_choice._INIT_CHOICE_TEXT = (
        "Когда кто-то свалится до 0 HP, бот спросит владельца одной из двух лечилок, тратить ли её. "
        "Если первый откажется, при наличии второй решение перейдёт к её владельцу."
    )

    # Persist the two-charge structure while retaining the legacy single-charge
    # field for backwards compatibility with old session records.
    original_ensure = campaign._ensure

    def ensure(session):
        original_ensure(session)
        _ensure_charges(session)

    campaign._ensure = ensure

    original_state = campaign._state

    def state(session):
        row = original_state(session)
        ensure(session)
        row["healing_charges"] = [dict(charge) for charge in session.healing_charges]
        return row

    campaign._state = state

    original_restore = campaign._restore_state

    def restore_state(session, data):
        original_restore(session, data)
        raw = (data or {}).get("healing_charges") if isinstance(data, dict) else None
        if isinstance(raw, list):
            session.healing_charges = [dict(item) for item in raw if isinstance(item, dict)]
        _ensure_charges(session)

    campaign._restore_state = restore_state

    # The existing initializer chooses charge #1. Add charge #2 after it finishes,
    # choosing a different owner whenever there is more than one participant.
    original_initialize = combat.initialize_party_combat

    async def initialize_party_combat(dnd_module, bot, session):
        result = await original_initialize(dnd_module, bot, session)
        user_ids = _participant_ids(session)
        if not user_ids:
            session.healing_charges = []
            return result
        first = _normalized_charge(getattr(session, "healing_charge", None), 1)
        if first is None:
            first = {"id": 1, "owner_id": random.choice(user_ids), "used": False, "lost": False}
        first["id"] = 1
        second_owner = _second_owner(user_ids, int(first["owner_id"]))
        session.healing_charges = [first]
        if second_owner is not None:
            session.healing_charges.append(
                {"id": 2, "owner_id": int(second_owner), "used": False, "lost": False}
            )
        _sync_legacy_charge(session)
        dnd_module.persist_dnd_sessions()
        if len(session.healing_charges) > 1:
            await bot.send_message(
                session.chat_id,
                f"🧪 Вторая одноразовая лечилка случайно досталась "
                f"{combat._participant_name(session, int(second_owner))}. Теперь у партии две попытки кого-нибудь не угробить.",
            )
        return result

    combat.initialize_party_combat = initialize_party_combat

    # Make status/profile output reflect both charges instead of the legacy alias.
    original_hero_lines = combat._hero_combat_lines

    def hero_combat_lines(session, user_id):
        lines = [
            line for line in original_hero_lines(session, user_id)
            if "Одноразовая аварийная лечилка" not in line
        ]
        owned = [
            charge for charge in _ensure_charges(session)
            if not charge.get("used") and not charge.get("lost")
            and int(charge.get("owner_id")) == int(user_id)
        ]
        if owned:
            lines.append(f"🧪 Аварийные лечилки при тебе: {len(owned)} шт., ещё целые.")
        return lines

    combat._hero_combat_lines = hero_combat_lines

    original_combat_context = combat._combat_context

    def combat_context(session):
        text = original_combat_context(session)
        text = re.sub(r"\nЛЕЧИЛКА:.*?\.$", "", text, flags=re.S)
        return text + f"\nЛЕЧИЛКИ: {_charges_text(combat, session)}."

    combat._combat_context = combat_context

    # Existing callback registration reads these module globals dynamically.
    healing_choice._heal_available = _heal_available
    healing_choice._resolve_enemy_attack_with_choice = _resolve_enemy_attack_with_choice
    healing_choice._apply_heal_decision = _apply_heal_decision

    original_continue = healing_choice._continue_after_decision

    async def continue_after_decision(dnd_module, callback, session, prompt):
        pending = getattr(session, "pending_heal_decision", None) or {}
        if pending:
            session.state = "WAITING_HEAL"
            dnd_module.persist_dnd_sessions()
            owner_name = combat._participant_name(session, int(pending["owner_id"]))
            target_name = combat._participant_name(session, int(pending["target_id"]))
            await callback.message.answer(
                f"🧪 {owner_name}, {target_name} всё ещё при смерти. Тратишь свою лечилку? "
                f"Доступно лечилок: {_available_count(session, target_id=int(pending['target_id']))}.",
                reply_markup=healing_choice._decision_keyboard(int(pending["nonce"])),
            )
            return None
        return await original_continue(dnd_module, callback, session, prompt)

    healing_choice._continue_after_decision = continue_after_decision

    # Replace the one-charge attack handler installed by dnd_healing_choice. For
    # non-attacks we still delegate to it, preserving the rest of the pipeline.
    inner_parse_turn = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if (
            not session
            or not dnd._is_participant_mode(session)
            or re.search(r"\[ACTION:END\]", str(response or ""), re.I)
        ):
            return await inner_parse_turn(bot, chat_id, response)
        attack = combat._parse_attack(response)
        if not attack:
            return await inner_parse_turn(bot, chat_id, response)

        clean, notices = campaign._apply_metadata(session, response)
        story = campaign._record_scene(session, clean)
        body = campaign.ACTION_RE.sub("", clean).strip()
        if body:
            if notices:
                body += "\n\n" + "\n".join(notices)
            await bot.send_message(chat_id, body)

        summary, continuation_prompt, _all_dead, pending = _resolve_enemy_attack_with_choice(
            combat, session, attack
        )
        await bot.send_message(chat_id, summary)
        dnd.persist_dnd_sessions()
        if story:
            campaign._maybe_image(dnd, bot, session, story)

        if pending:
            decision = session.pending_heal_decision
            session.state = "WAITING_HEAL"
            dnd.persist_dnd_sessions()
            owner_name = combat._participant_name(session, int(decision["owner_id"]))
            target_name = combat._participant_name(session, int(decision["target_id"]))
            await bot.send_message(
                chat_id,
                f"🧪 {owner_name}, {target_name} щас помрёт. Тратишь свою лечилку? "
                f"Доступно лечилок: {_available_count(session, target_id=int(decision['target_id']))}.",
                reply_markup=healing_choice._decision_keyboard(int(decision["nonce"])),
            )
            return None

        try:
            next_response = await dnd.generate_session_response(
                session,
                dnd.with_scene_direction(session, continuation_prompt),
            )
            return await dnd.parse_and_execute_turn(bot, chat_id, next_response)
        except Exception:
            logging.exception("DnD two-heal enemy attack continuation failed chat_id=%s", chat_id)
            await bot.send_message(chat_id, "Мастер завис после мордобоя. Егра сохранена.")
            return await dnd.open_action_window(bot, chat_id)

    dnd.parse_and_execute_turn = parse_turn
    dnd_router._upupa_dnd_two_heals_configured = True
