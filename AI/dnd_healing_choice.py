"""Player-controlled decision for the single DnD emergency heal."""
from __future__ import annotations

import logging
import random
import re

from aiogram import F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


_AUTO_HEAL_RULE = (
    "При 0 HP герой погибает и больше не участвует в ходах. В партии ровно одна одноразовая аварийная лечилка: "
    "она заранее случайно лежит у одного героя и автоматически спасает первого героя, который упал бы до 0 HP, после чего исчезает."
)
_CHOICE_HEAL_RULE = (
    "При падении героя до 0 HP он сначала оказывается при смерти и не участвует в ходах. "
    "Если одноразовая аварийная лечилка ещё существует, код отдельно спрашивает её владельца, тратить ли её на этого героя. "
    "Только владелец решает: при согласии лечилка расходуется и герой возвращается в игру; при отказе этот герой погибает. "
    "Отказ лечить другого героя не расходует лечилку, поэтому при следующем падении до 0 HP владельца спросят снова. "
    "Если владелец отказался спасать самого себя и погиб, лечилка теряется вместе с ним. Не принимай это решение за игрока."
)
_INIT_AUTO_TEXT = "Она автоматически спасёт первого героя, который свалится до 0 HP."
_INIT_CHOICE_TEXT = (
    "Когда кто-то свалится до 0 HP, бот спросит владельца, тратить ли её. "
    "Можно спасти героя, а можно оставить помирать и приберечь лечилку на потом."
)


def _decision_keyboard(nonce: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💉 Лечить", callback_data=f"dnd:heal:yes:{nonce}"),
                InlineKeyboardButton(text="☠️ Не лечить", callback_data=f"dnd:heal:no:{nonce}"),
            ]
        ]
    )


def _heal_available(session) -> bool:
    charge = getattr(session, "healing_charge", {}) or {}
    return charge.get("owner_id") is not None and not bool(charge.get("used"))


def _resolve_enemy_attack_with_choice(combat, session, attack: dict):
    """Resolve an attack, but pause a lethal result for the healer's decision."""
    living = sorted(combat._living_ids(session))
    if not living:
        summary, prompt, all_dead = combat._resolve_enemy_attack(session, attack)
        return summary, prompt, all_dead, False

    attack = dict(attack)
    target_id = attack.get("target_user_id")
    if target_id not in living:
        target_id = combat.random.choice(living)
        attack["target_user_id"] = target_id

    charge = getattr(session, "healing_charge", {}) or {}
    owner_id = charge.get("owner_id")
    available = _heal_available(session)
    previous_used = bool(charge.get("used"))

    # The combat layer currently auto-consumes the charge. Temporarily hide it so
    # the attack can resolve damage normally; if the hit is lethal, we turn death
    # into a pending player decision below.
    if available:
        charge["used"] = True
        session.healing_charge = charge
    try:
        summary, prompt, all_dead = combat._resolve_enemy_attack(session, attack)
    finally:
        if available:
            charge["used"] = previous_used
            session.healing_charge = charge

    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(int(target_id)))
    lethal = isinstance(sheet, dict) and int(sheet.get("hp", 0)) <= 0 and str(sheet.get("status")) == "dead"
    if not (available and lethal and owner_id is not None):
        return summary, prompt, all_dead, False

    sheet["hp"] = 0
    sheet["status"] = "dying"
    nonce = random.randint(100000, 999999)
    session.pending_heal_decision = {
        "target_id": int(target_id),
        "owner_id": int(owner_id),
        "nonce": nonce,
        "attack_prompt": str(prompt),
        "reason": str(attack.get("reason") or "враг атакует"),
    }

    lines = [line for line in str(summary).splitlines() if not line.startswith("☠️ ")]
    lines.append(
        f"💀 {combat._participant_name(session, target_id)} при смерти на 0 HP. "
        f"Теперь решает {combat._participant_name(session, int(owner_id))} с лечилкой."
    )
    return "\n".join(lines), prompt, False, True


def _apply_heal_decision(combat, session, *, use_heal: bool):
    pending = getattr(session, "pending_heal_decision", None) or {}
    target_id = int(pending["target_id"])
    owner_id = int(pending["owner_id"])
    target_name = combat._participant_name(session, target_id)
    owner_name = combat._participant_name(session, owner_id)
    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(target_id))
    if not isinstance(sheet, dict):
        session.pending_heal_decision = None
        return "Решение протухло: героя уже нет в боевом состоянии.", "Продолжай сюжет.", False

    attack_prompt = str(pending.get("attack_prompt") or "")
    charge = getattr(session, "healing_charge", {}) or {}

    if use_heal:
        heal_note = combat._consume_heal(session, target_id)
        if heal_note:
            result_text = heal_note
            correction = (
                f"ВАЖНО: вместо гибели {target_name} владелец лечилки {owner_name} решил её потратить. "
                f"{target_name} снова жив с {sheet.get('hp', 0)} HP; лечилка израсходована."
            )
        else:
            sheet["hp"] = 0
            sheet["status"] = "dead"
            result_text = f"☠️ Лечилка куда-то делась, и {target_name} всё-таки погиб."
            correction = f"{target_name} погиб: лечилка оказалась недоступна."
    else:
        sheet["hp"] = 0
        sheet["status"] = "dead"
        if target_id == owner_id:
            charge["used"] = True
            charge["lost"] = True
            session.healing_charge = charge
            result_text = (
                f"☠️ {owner_name} решил не спасать даже себя. {target_name} погиб, "
                "а лечилка пропала вместе с владельцем. Красиво, блядь."
            )
            correction = (
                f"{owner_name} отказался использовать лечилку на себя, погиб, и лечилка потеряна вместе с ним."
            )
        else:
            result_text = (
                f"☠️ {owner_name} решил не тратить лечилку на {target_name}. "
                f"{target_name} погиб. Лечилка осталась у {owner_name} на будущее."
            )
            correction = (
                f"{owner_name} отказался лечить {target_name}; {target_name} погиб. "
                "Лечилка НЕ израсходована и остаётся у владельца."
            )

    session.pending_heal_decision = None
    all_dead = not combat._living_ids(session)
    prompt = (attack_prompt + " " + correction).strip()
    if all_dead:
        prompt += " Вся партия погибла: дай короткий финал и закончи [ACTION:END]."
    else:
        prompt += " Продолжай сюжет с этим точным решением игрока."
    return result_text, prompt, all_dead


class _InitMessageProxy:
    def __init__(self, bot):
        self._bot = bot

    async def send_message(self, chat_id, text, *args, **kwargs):
        text = str(text).replace(_INIT_AUTO_TEXT, _INIT_CHOICE_TEXT)
        return await self._bot.send_message(chat_id, text, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._bot, name)


async def _continue_after_decision(dnd, callback, session, prompt: str) -> None:
    try:
        response = await dnd.generate_session_response(
            session,
            dnd.with_scene_direction(session, prompt),
        )
        await dnd.parse_and_execute_turn(callback.bot, session.chat_id, response)
    except Exception:
        logging.exception("DnD healing decision continuation failed chat_id=%s", session.chat_id)
        await callback.message.answer("Мастер завис после решения с лечилкой. Егра сохранена.")
        await dnd.open_action_window(callback.bot, session.chat_id)


def install_dnd_healing_choice(dnd_router) -> None:
    if getattr(dnd_router, "_upupa_dnd_healing_choice_configured", False):
        return

    from AI import dnd
    from AI import dnd_campaign as campaign
    from AI import dnd_combat as combat
    from AI import dnd_state_commands as state_commands

    combat.COMBAT_RULES = combat.COMBAT_RULES.replace(_AUTO_HEAL_RULE, _CHOICE_HEAL_RULE)
    dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.replace(_AUTO_HEAL_RULE, _CHOICE_HEAL_RULE)

    original_ensure = campaign._ensure

    def ensure(session):
        original_ensure(session)
        pending = getattr(session, "pending_heal_decision", None)
        if pending is not None and not isinstance(pending, dict):
            session.pending_heal_decision = None
        elif not hasattr(session, "pending_heal_decision"):
            session.pending_heal_decision = None

    campaign._ensure = ensure

    original_state = campaign._state

    def state(session):
        row = original_state(session)
        ensure(session)
        row["pending_heal_decision"] = session.pending_heal_decision
        return row

    campaign._state = state

    original_initialize = combat.initialize_party_combat

    async def initialize_party_combat(dnd_module, bot, session):
        return await original_initialize(dnd_module, _InitMessageProxy(bot), session)

    combat.initialize_party_combat = initialize_party_combat

    state_commands._STATE_LABELS["WAITING_HEAL"] = "владелец лечилки решает, спасать ли умирающего"

    original_hero_lines = combat._hero_combat_lines

    def hero_combat_lines(session, user_id):
        lines = original_hero_lines(session, user_id)
        sheet = combat._active_sheet(session, user_id)
        if isinstance(sheet, dict) and str(sheet.get("status")) == "dying":
            lines = [line.replace("(погиб)", "(при смерти)") for line in lines]
        return lines

    combat._hero_combat_lines = hero_combat_lines

    original_render_status = state_commands.render_status

    def render_status(dnd_module, chat_id):
        text = original_render_status(dnd_module, chat_id)
        session = dnd_module.dnd_sessions.get(int(chat_id))
        pending = getattr(session, "pending_heal_decision", None) if session else None
        if pending:
            target = combat._participant_name(session, int(pending["target_id"]))
            owner = combat._participant_name(session, int(pending["owner_id"]))
            text += f"\n\n🧪 {target} при смерти. Ждём решения {owner}: лечить или нет."
        return text

    state_commands.render_status = render_status

    original_parse_turn = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if (
            not session
            or not dnd._is_participant_mode(session)
            or re.search(r"\[ACTION:END\]", str(response or ""), re.I)
        ):
            return await original_parse_turn(bot, chat_id, response)

        attack = combat._parse_attack(response)
        if not attack:
            return await original_parse_turn(bot, chat_id, response)

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
                f"🧪 {owner_name}, {target_name} щас помрёт. Тратишь на него единственную лечилку?",
                reply_markup=_decision_keyboard(int(decision["nonce"])),
            )
            return None

        try:
            next_response = await dnd.generate_session_response(
                session,
                dnd.with_scene_direction(session, continuation_prompt),
            )
            return await dnd.parse_and_execute_turn(bot, chat_id, next_response)
        except Exception:
            logging.exception("DnD enemy attack continuation failed chat_id=%s", chat_id)
            await bot.send_message(chat_id, "Мастер завис после мордобоя. Егра сохранена.")
            return await dnd.open_action_window(bot, chat_id)

    dnd.parse_and_execute_turn = parse_turn

    async def heal_callback(callback):
        if not callback.message:
            await callback.answer("Кнопка потерялась.")
            return
        session = dnd.dnd_sessions.get(callback.message.chat.id)
        pending = getattr(session, "pending_heal_decision", None) if session else None
        if not session or session.state != "WAITING_HEAL" or not pending:
            await callback.answer("Это решение уже протухло.", show_alert=True)
            return
        if int(callback.from_user.id) != int(pending["owner_id"]):
            owner = combat._participant_name(session, int(pending["owner_id"]))
            await callback.answer(f"Лечилка не твоя. Решает {owner}.", show_alert=True)
            return

        parts = str(callback.data or "").split(":")
        if len(parts) != 4 or not parts[3].isdigit() or int(parts[3]) != int(pending["nonce"]):
            await callback.answer("Старая кнопка. Уже другое решение.", show_alert=True)
            return
        use_heal = parts[2] == "yes"
        if parts[2] not in {"yes", "no"}:
            await callback.answer("Кнопка сломалась.", show_alert=True)
            return

        result_text, prompt, _all_dead = _apply_heal_decision(combat, session, use_heal=use_heal)
        session.state = "RESOLVING"
        dnd.persist_dnd_sessions()
        await callback.answer("Решено.")
        try:
            await callback.message.edit_text(result_text)
        except Exception:
            await callback.message.answer(result_text)
        await _continue_after_decision(dnd, callback, session, prompt)

    dnd_router.callback_query.register(heal_callback, F.data.startswith("dnd:heal:"))
    dnd_router._upupa_dnd_healing_choice_configured = True
