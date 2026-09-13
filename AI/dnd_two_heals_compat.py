"""Compatibility shims around the two-heal DnD extension."""
from __future__ import annotations


def _legacy_apply_heal_decision(combat, session, *, use_heal: bool):
    """Resolve old persisted/test pending decisions that predate charge_id."""
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


def install_dnd_two_heals_compat() -> None:
    """Keep old pending-decision records valid while new games use two charges."""
    from AI import dnd_combat as combat
    from AI import dnd_healing_choice as healing_choice
    from AI import dnd_two_heals as two_heals

    if getattr(two_heals, "_upupa_dnd_two_heals_compat_installed", False):
        return

    two_apply = healing_choice._apply_heal_decision

    def apply_heal_decision(combat_module, session, *, use_heal: bool):
        pending = getattr(session, "pending_heal_decision", None) or {}
        if isinstance(pending, dict) and "charge_id" not in pending:
            return _legacy_apply_heal_decision(combat_module, session, use_heal=use_heal)
        return two_apply(combat_module, session, use_heal=use_heal)

    healing_choice._apply_heal_decision = apply_heal_decision

    def available_count(session, *, target_id=None):
        pending = getattr(session, "pending_heal_decision", None) or {}
        declined = {
            int(value)
            for value in (pending.get("declined_charge_ids") or [])
            if str(value).lstrip("-").isdigit()
        }
        count = 0
        for charge in two_heals._ensure_charges(session):
            if (
                int(charge.get("id", 0)) in declined
                or charge.get("used")
                or charge.get("lost")
                or charge.get("owner_id") is None
            ):
                continue
            if two_heals._owner_can_use(session, int(charge["owner_id"]), target_id):
                count += 1
        return count

    # Functions defined in dnd_two_heals resolve this module global dynamically,
    # so replacing it also fixes the count shown after a first owner declines.
    two_heals._available_count = available_count
    two_heals._upupa_dnd_two_heals_compat_installed = True
