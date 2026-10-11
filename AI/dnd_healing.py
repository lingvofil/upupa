"""Emergency heals: one explicit module instead of five layered versions.

History: one auto-heal (dnd_combat) → a player's choice (dnd_healing_choice) →
two charges (dnd_two_heals) → compatibility shims (dnd_two_heals_compat) →
one charge per three heroes (dnd_scaled_heals) → «лечить» outside fights
(dnd_manual_healing). Each version monkeypatched the previous one. Two real
defects came out of that stack:

* a master's ENEMY_ATTACK was handled by the two-heals wrapper, which shadowed
  the durable enemy-attack transaction in dnd_combat (provider failure after
  the damage could replay the enemy's roll);
* a simple enemy resolved by the local engine still called the legacy
  ``_consume_heal`` on the single ``healing_charge`` alias, which the charge
  list then overwrote: the hero was saved for free, without the owner's say.

Now ``dnd_combat._resolve_enemy_attack`` calls ``begin_dying`` for every lethal
hit, from any path, and the owner's decision continues through a durable
generation request.

Rules: one charge per three heroes (rounded up), dealt to different owners
while possible. A hero at 0 HP is *dying*: owners of available charges are
asked in turn. Yes spends that charge and revives the hero. Declining to save
someone else keeps the charge and passes the decision on; declining to save
yourself loses your charge. Nobody left — the hero dies. Out of a crisis the
owner may spend a charge on a wounded living hero with «лечить».
"""
from __future__ import annotations

import random
import re
import secrets

from aiogram import BaseMiddleware, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

HEAL_RULE = (
    "При падении героя до 0 HP он сначала оказывается при смерти и не участвует в ходах. "
    "Количество одноразовых аварийных лечилок задаёт код по размеру партии: одна лечилка на каждые три героя "
    "с округлением вверх (1–3 героя — 1; 4–6 — 2; 7–9 — 3 и так далее). "
    "Когда герой при смерти, код по очереди спрашивает владельцев доступных лечилок, тратить ли одну на него. "
    "Вне кризиса владелец сам лечит раненого командой «лечить». Не предлагай лечение вариантом POLL, "
    "не создавай дополнительные лечилки и не лечи HP повествовательно без явной игровой причины."
)
DECISION_KIND = "HEAL_DECISION_CONTINUATION"
_TARGETS_RE = re.compile(r"(?:^|;)TARGETS:([0-9,]+)(?:;|$)", re.I)
_POLL_ACTION_RE = re.compile(r"\[ACTION:(POLL;[^\]]*?OPTIONS:[^\]]+)\]", re.I | re.S)
_HEAL_OPTION_RE = re.compile(
    r"(?:\bлеч\w*|\bвылеч\w*|\bхил\w*|\bаптеч\w*|\bлечил\w*|восстанов\w*\s+(?:hp|хп|здоров\w*))", re.I)


def _name(session, user_id) -> str:
    from AI.dnd_combat import _participant_name

    return _participant_name(session, int(user_id))


def _participant_ids(session) -> list[int]:
    result = []
    for item in (getattr(session, "participants", {}) or {}).values():
        try:
            result.append(int(item["user_id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _sheet(session, user_id):
    row = (getattr(session, "character_sheets", {}) or {}).get(str(int(user_id)))
    return row if isinstance(row, dict) else None


# ----------------------------------------------------------------- charges

def _nonce() -> int:
    """Button nonce; independent of game dice."""
    return 100000 + secrets.randbelow(900000)


def charge_count(party_size) -> int:
    try:
        count = max(0, int(party_size))
    except (TypeError, ValueError):
        return 0
    return (count + 2) // 3 if count else 0


def _normalized(raw, charge_id: int) -> dict | None:
    if not isinstance(raw, dict) or raw.get("owner_id") is None:
        return None
    try:
        owner_id = int(raw["owner_id"])
    except (TypeError, ValueError):
        return None
    return {"id": int(raw.get("id") or charge_id), "owner_id": owner_id,
            "used": bool(raw.get("used")), "lost": bool(raw.get("lost"))}


def _sync_alias(session) -> None:
    """Keep the legacy single-charge field readable by old records and views."""
    charges = getattr(session, "healing_charges", None) or []
    first = charges[0] if charges else None
    if first:
        session.healing_charge = {"owner_id": first.get("owner_id"), "used": bool(first.get("used")),
                                  "lost": bool(first.get("lost"))}
    else:
        session.healing_charge = {"owner_id": None, "used": True}


def _next_owner(user_ids, occupied):
    free = [uid for uid in user_ids if uid not in occupied]
    return random.choice(free or user_ids) if user_ids else None


def ensure_charges(session) -> list[dict]:
    """The party's quota, migrating one-/two-charge saves and resizing to the party."""
    user_ids = _participant_ids(session)
    if not user_ids or not (getattr(session, "character_sheets", None) or {}):
        session.healing_charges = []
        if not user_ids:
            session.healing_charge = {"owner_id": None, "used": True}
        return session.healing_charges
    desired = charge_count(len(user_ids))
    charges = []
    raw = getattr(session, "healing_charges", None)
    for index, item in enumerate(raw if isinstance(raw, list) else [], start=1):
        normalized = _normalized(item, index)
        if normalized and normalized["owner_id"] in user_ids:
            charges.append(normalized)
        if len(charges) >= desired:
            break
    if not charges:
        legacy = _normalized(getattr(session, "healing_charge", None), 1)
        if legacy and legacy["owner_id"] in user_ids:
            charges.append(legacy)
    occupied = {charge["owner_id"] for charge in charges}
    while len(charges) < desired:
        owner = _next_owner(user_ids, occupied)
        if owner is None:
            break
        charges.append({"id": 0, "owner_id": int(owner), "used": False, "lost": False})
        occupied.add(int(owner))
    session.healing_charges = charges[:desired]
    for index, charge in enumerate(session.healing_charges, start=1):
        charge["id"] = index
    _sync_alias(session)
    return session.healing_charges


def deal_charges(session) -> str:
    """Fresh party: deal the quota to different owners; returns the announcement."""
    session.healing_charges = []
    session.healing_charge = {"owner_id": None, "used": True}
    charges = ensure_charges(session)
    if not charges:
        return ""
    owners = ", ".join(_name(session, charge["owner_id"]) for charge in charges)
    noun = "лечилка досталась" if len(charges) == 1 else f"лечилки ({len(charges)}) достались"
    return (f"🧪 Одноразовая аварийная {noun}: {owners}. Когда кто-то свалится до 0 HP, бот спросит владельца, "
            "тратить ли её; вне боя владелец может подлечить раненого командой «лечить».")


def owner_can_use(session, owner_id, target_id=None) -> bool:
    sheet = _sheet(session, owner_id)
    if sheet is None:
        return True
    status = str(sheet.get("status") or "alive").casefold()
    try:
        hp = int(sheet.get("hp", 0))
    except (TypeError, ValueError):
        hp = 0
    if target_id is not None and int(owner_id) == int(target_id) and status in {"dead", "dying"}:
        return True  # a dying owner may still save themself
    return status != "dead" and hp > 0


def available_charge(session, *, target_id=None, excluded_ids=()) -> dict | None:
    excluded = {int(value) for value in excluded_ids}
    for charge in ensure_charges(session):
        if charge["id"] in excluded or charge["used"] or charge["lost"]:
            continue
        if owner_can_use(session, charge["owner_id"], target_id):
            return charge
    return None


def available_count(session, *, target_id=None) -> int:
    pending = getattr(session, "pending_heal_decision", None) or {}
    declined = {int(value) for value in (pending.get("declined_charge_ids") or []) if str(value).lstrip("-").isdigit()}
    return sum(1 for charge in ensure_charges(session)
               if charge["id"] not in declined and not charge["used"] and not charge["lost"]
               and owner_can_use(session, charge["owner_id"], target_id))


def charge_by_id(session, charge_id) -> dict | None:
    return next((charge for charge in ensure_charges(session) if charge["id"] == int(charge_id or 0)), None)


def owned_charge(session, owner_id) -> dict | None:
    return next((charge for charge in ensure_charges(session)
                 if not charge["used"] and not charge["lost"] and charge["owner_id"] == int(owner_id)), None)


def charges_text(session) -> str:
    chunks = []
    for charge in ensure_charges(session):
        status = ("потеряна" if charge["lost"] else "использована" if charge["used"]
                  else f"у ID {charge['owner_id']} ({_name(session, charge['owner_id'])})")
        chunks.append(f"#{charge['id']} {status}")
    return "; ".join(chunks) or "нет"


# ----------------------------------------------------------------- dying and decisions

def begin_dying(session, target_id, *, reason="враг атакует") -> str | None:
    """A lethal hit: mark the hero dying and open a decision if a charge can save them.

    Returns the summary line for the dying hero, or None when nobody can help
    (the caller then resolves an ordinary death).
    """
    charge = available_charge(session, target_id=int(target_id))
    sheet = _sheet(session, target_id)
    if charge is None or sheet is None:
        return None
    sheet["hp"] = 0
    sheet["status"] = "dying"
    session.pending_heal_decision = {
        "target_id": int(target_id), "owner_id": int(charge["owner_id"]), "charge_id": int(charge["id"]),
        "declined_charge_ids": [], "nonce": _nonce(), "attack_prompt": "",
        "reason": str(reason or "враг атакует"),
    }
    return (f"💀 {_name(session, target_id)} при смерти на 0 HP. "
            f"Теперь решает {_name(session, charge['owner_id'])} с лечилкой.")


def decision_text(session) -> str:
    pending = getattr(session, "pending_heal_decision", None) or {}
    owner, target = _name(session, pending["owner_id"]), _name(session, pending["target_id"])
    return (f"🧪 {owner}, {target} щас помрёт. Тратишь свою лечилку? "
            f"Доступно лечилок: {available_count(session, target_id=int(pending['target_id']))}.")


def keyboard(nonce: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💉 Лечить", callback_data=f"dnd:heal:yes:{nonce}"),
        InlineKeyboardButton(text="☠️ Не лечить", callback_data=f"dnd:heal:no:{nonce}"),
    ]])


def _living(session) -> list[int]:
    from AI.dnd_combat import _living_ids

    return list(_living_ids(session))


def _death(session, pending, sheet, note: str):
    sheet["hp"] = 0
    sheet["status"] = "dead"
    session.pending_heal_decision = None
    target = _name(session, pending["target_id"])
    all_dead = not _living(session)
    prompt = (str(pending.get("attack_prompt") or "") + f" {target} погиб после решений владельцев лечилок.").strip()
    prompt += (" Вся партия погибла: дай короткий финал и закончи [ACTION:END]." if all_dead
               else " Продолжай сюжет с этим точным решением игроков.")
    return f"☠️ {note} {target} погиб.", prompt, all_dead


def apply_decision(session, *, use_heal: bool, rng=None):
    """Owner's answer → (public text, continuation prompt, all_dead).

    A still-open ``pending_heal_decision`` afterwards means the next owner is asked.
    Decisions saved before charge ids existed resolve against the owner's charge.
    """
    rng = rng or random
    pending = dict(getattr(session, "pending_heal_decision", None) or {})
    target_id, owner_id = int(pending["target_id"]), int(pending["owner_id"])
    sheet = _sheet(session, target_id)
    if sheet is None:
        session.pending_heal_decision = None
        return "Решение протухло: героя уже нет в боевом состоянии.", "Продолжай сюжет.", False
    target_name, owner_name = _name(session, target_id), _name(session, owner_id)
    charge = charge_by_id(session, pending.get("charge_id")) if "charge_id" in pending else owned_charge(session, owner_id)
    if not charge or charge["used"] or charge["lost"]:
        charge = available_charge(session, target_id=target_id, excluded_ids=pending.get("declined_charge_ids") or [])
        if not charge:
            return _death(session, pending, sheet, "Лечилки кончились или потерялись.")
        owner_id, owner_name = charge["owner_id"], _name(session, charge["owner_id"])

    if use_heal:
        restored = max(1, min(int(sheet.get("max_hp", 10) or 10), rng.randint(1, 8) + 2))
        sheet.update(hp=restored, status="alive")
        charge["used"] = True
        session.pending_heal_decision = None
        _sync_alias(session)
        text = (f"🧪 {owner_name} тратит свою аварийную лечилку: {target_name} возвращается с {restored} HP. "
                "Эта лечилка израсходована.")
        prompt = (str(pending.get("attack_prompt") or "")
                  + f" ВАЖНО: {owner_name} потратил одну из аварийных лечилок на {target_name}; {target_name} снова жив "
                    f"с {restored} HP, лечилка израсходована. Продолжай сюжет с этим точным решением игрока.").strip()
        return text, prompt, False

    declined = [int(value) for value in (pending.get("declined_charge_ids") or [])]
    if charge["id"] not in declined:
        declined.append(charge["id"])
    self_refusal = target_id == owner_id
    if self_refusal:
        charge.update(used=True, lost=True)
        _sync_alias(session)
    following = available_charge(session, target_id=target_id, excluded_ids=declined)
    if following:
        session.pending_heal_decision = {**pending, "owner_id": following["owner_id"], "charge_id": following["id"],
                                         "declined_charge_ids": declined, "nonce": _nonce()}
        next_name = _name(session, following["owner_id"])
        text = (f"☠️ {owner_name} отказался тратить свою лечилку на себя — она потеряна. Но есть ещё лечилка: теперь решает {next_name}."
                if self_refusal else
                f"🧪 {owner_name} решил приберечь свою лечилку и не спасать {target_name}. Теперь решает {next_name} со следующей лечилкой.")
        return text, str(pending.get("attack_prompt") or ""), False
    note = (f"{owner_name} отказался спасать себя, и его лечилка пропала. Другой помощи не осталось."
            if self_refusal else f"{owner_name} не стал тратить лечилку, и другой доступной помощи не осталось.")
    return _death(session, pending, sheet, note)


async def enter_decision(dnd, bot, session) -> None:
    """WAITING_HEAL: ask the current owner (bot may be a durable proxy)."""
    pending = session.pending_heal_decision
    session.state = "WAITING_HEAL"
    dnd.persist_dnd_sessions()
    await bot.send_message(session.chat_id, decision_text(session), reply_markup=keyboard(int(pending["nonce"])))


async def _continue_after_decision(dnd, bot, session, request_id) -> None:
    from AI.dnd_ai_budget import dnd_turn_budget
    from AI.dnd_local_runtime import game_lock
    from AI.dnd_result_recovery import continue_pending_generation

    async with game_lock(session.chat_id):
        if (dnd.dnd_sessions.get(session.chat_id) is not session or getattr(session, "paused", False)
                or (getattr(session, "pending_generation_request", None) or {}).get("id") != request_id):
            return
        with dnd_turn_budget(session, turn_id=f"heal:{request_id}"):
            completed = await continue_pending_generation(dnd, bot, session)
        if not completed:
            await bot.send_message(session.chat_id, "Решение с лечилкой сохранено. Ведущий может написать «днд дальше».")


async def handle_decision_callback(callback) -> None:
    from AI import dnd
    from AI.dnd_result_recovery import reserve_generation_request

    if not callback.message:
        await callback.answer("Кнопка потерялась.")
        return
    session = dnd.dnd_sessions.get(callback.message.chat.id)
    pending = getattr(session, "pending_heal_decision", None) if session else None
    if not session or session.state != "WAITING_HEAL" or not pending:
        await callback.answer("Это решение уже протухло.", show_alert=True)
        return
    if int(callback.from_user.id) != int(pending["owner_id"]):
        await callback.answer(f"Лечилка не твоя. Решает {_name(session, pending['owner_id'])}.", show_alert=True)
        return
    parts = str(callback.data or "").split(":")
    if len(parts) != 4 or parts[2] not in {"yes", "no"} or not parts[3].isdigit() or int(parts[3]) != int(pending["nonce"]):
        await callback.answer("Старая кнопка. Уже другое решение.", show_alert=True)
        return
    text, prompt, _all_dead = apply_decision(session, use_heal=parts[2] == "yes")
    if getattr(session, "pending_heal_decision", None):
        dnd.persist_dnd_sessions()
        await callback.answer("Передаём решение дальше.")
        await _edit_or_answer(callback, text)
        await enter_decision(dnd, callback.bot, session)
        return
    session.state = "RESOLVING"
    # Durable: the decision and its continuation are committed before any
    # provider call; «днд дальше» finishes it after a failure or restart.
    reserve_generation_request(session, dnd.with_scene_direction(session, prompt), kind=DECISION_KIND)
    dnd.persist_dnd_sessions()
    await callback.answer("Решено.")
    await _edit_or_answer(callback, text)
    request_id = (getattr(session, "pending_generation_request", None) or {}).get("id")
    dnd._start_background_task(_continue_after_decision(dnd, callback.bot, session, request_id),
                               name=f"dnd-heal-decision:{session.chat_id}:{request_id}")


async def _edit_or_answer(callback, text):
    try:
        await callback.message.edit_text(text)
    except Exception:
        await callback.message.answer(text)


# ----------------------------------------------------------------- «лечить»

def strip_healing_poll_options(response: str) -> str:
    """Healing has its own command/UI; never a model poll option."""
    def replace(match):
        command = match.group(1)
        marker = command.upper().find("OPTIONS:")
        if marker < 0:
            return match.group(0)
        prefix = command[: marker + len("OPTIONS:")]
        options = [item.strip() for item in command[marker + len("OPTIONS:"):].split(";") if item.strip()]
        kept = [item for item in options if not _HEAL_OPTION_RE.search(item)]
        if len(kept) == len(options):
            return match.group(0)
        if len(kept) >= 2:
            return f"[ACTION:{prefix}{';'.join(kept)}]"
        targets = _TARGETS_RE.search(command)
        return f"[ACTION:INPUT;TARGETS:{targets.group(1)}]" if targets else "[ACTION:INPUT]"

    return _POLL_ACTION_RE.sub(replace, str(response or ""))


def wounded_targets(session) -> list[tuple[int, int, int]]:
    rows = []
    for raw_id, sheet in (getattr(session, "character_sheets", {}) or {}).items():
        if not isinstance(sheet, dict):
            continue
        try:
            user_id, hp, max_hp = int(raw_id), int(sheet.get("hp", 0)), int(sheet.get("max_hp", 0))
        except (TypeError, ValueError):
            continue
        if str(sheet.get("status") or "alive").casefold() == "alive" and 0 < hp < max_hp:
            rows.append((user_id, hp, max_hp))
    return rows


def apply_manual_heal(session, owner_id, charge_id, target_id, *, roll=None):
    charge = charge_by_id(session, charge_id)
    if not charge or charge["used"] or charge["lost"]:
        return False, "Эта лечилка уже недоступна."
    if charge["owner_id"] != int(owner_id):
        return False, "Это не твоя лечилка."
    if not owner_can_use(session, owner_id):
        return False, "Сейчас ты не можешь использовать лечилку."
    sheet = _sheet(session, target_id)
    if sheet is None:
        return False, "Такого героя сейчас нет в партии."
    try:
        hp, max_hp = int(sheet.get("hp", 0)), int(sheet.get("max_hp", 0))
    except (TypeError, ValueError):
        return False, "У героя сломано состояние здоровья."
    if str(sheet.get("status") or "alive").casefold() != "alive" or hp <= 0:
        return False, "Герой при смерти или погиб — тут работает аварийное спасение, а не команда «лечить»."
    if hp >= max_hp:
        return False, "У героя и так полное здоровье."
    amount = max(1, int(roll) if roll is not None else random.randint(1, 8) + 2)
    sheet["hp"] = min(max_hp, hp + amount)
    charge["used"] = True
    _sync_alias(session)
    target = _name(session, target_id)
    return True, (f"🧪 {_name(session, owner_id)} тратит лечилку на {target}: +{sheet['hp'] - hp} HP. "
                  f"❤️ {target}: {sheet['hp']}/{max_hp}. Лечилка израсходована.")


class DndManualHealingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from AI import dnd

        callback_data = str(getattr(event, "data", None) or "")
        if callback_data.startswith("dnd:manual_heal:"):
            parts = callback_data.split(":")
            if len(parts) != 5 or not all(part.isdigit() for part in parts[2:]):
                await event.answer("Кнопка протухла.", show_alert=True)
                return None
            owner_id, charge_id, target_id = map(int, parts[2:])
            actor_id = getattr(getattr(event, "from_user", None), "id", None)
            if actor_id is None or int(actor_id) != owner_id:
                await event.answer("Это не твоя лечилка.", show_alert=True)
                return None
            message = getattr(event, "message", None)
            chat_id = getattr(getattr(message, "chat", None), "id", None)
            session = dnd.dnd_sessions.get(int(chat_id)) if chat_id is not None else None
            if not session or not dnd._is_participant_mode(session):
                await event.answer("Партия уже закончилась.", show_alert=True)
                return None
            ok, text = apply_manual_heal(session, owner_id, charge_id, target_id)
            if not ok:
                await event.answer(text, show_alert=True)
                return None
            dnd.persist_dnd_sessions()
            await event.answer("Подлечили.")
            if message is not None:
                await message.edit_text(text)
            return None
        if str(getattr(event, "text", None) or "").strip().casefold() != "лечить":
            return await handler(event, data)
        chat, actor = getattr(event, "chat", None), getattr(event, "from_user", None)
        if chat is None or actor is None:
            return await handler(event, data)
        session = dnd.dnd_sessions.get(int(chat.id))
        if not session or not dnd._is_participant_mode(session) or str(getattr(session, "state", "")) == "LOBBY":
            await event.answer("Команда «лечить» работает во время партии с участниками.")
            return None
        owner_id = int(actor.id)
        if owner_id not in dnd._participant_ids(session):
            await event.answer("Ты не участник этой партии.")
            return None
        if getattr(session, "pending_heal_decision", None):
            await event.answer("Сначала решите текущее аварийное спасение героя при смерти.")
            return None
        charge = owned_charge(session, owner_id)
        if not charge:
            await event.answer("У тебя нет доступной лечилки.")
            return None
        targets = wounded_targets(session)
        if not targets:
            await event.answer("Все живые герои и так с полным здоровьем — лечилку пока не тратим.")
            return None
        rows = [[InlineKeyboardButton(text=f"{_name(session, uid)} · {hp}/{max_hp} HP",
                                      callback_data=f"dnd:manual_heal:{owner_id}:{charge['id']}:{uid}")]
                for uid, hp, max_hp in targets]
        await event.answer("🧪 Кого лечить? Лечилка одноразовая.", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return None


def status_line(session) -> str:
    pending = getattr(session, "pending_heal_decision", None) if session else None
    if not pending:
        return ""
    return (f"🧪 {_name(session, pending['target_id'])} при смерти. "
            f"Ждём решения {_name(session, pending['owner_id'])}: лечить или нет.")


# ----------------------------------------------------------------- install

def install_dnd_healing(router, *, state_policy) -> None:
    from AI import dnd_state_commands as state_commands

    if getattr(router, "_upupa_dnd_healing_installed", False):
        return

    def ensure(session):
        pending = getattr(session, "pending_heal_decision", None)
        if not isinstance(pending, dict):
            session.pending_heal_decision = None
        ensure_charges(session)

    state_policy.add_ensure_hook(ensure)
    state_policy.add_state_field("pending_heal_decision", lambda session: getattr(session, "pending_heal_decision", None))
    state_policy.add_state_field("healing_charges", lambda session: [dict(charge) for charge in ensure_charges(session)])
    state_policy.add_restore_hook(lambda session, _data: ensure(session))
    state_commands._STATE_LABELS["WAITING_HEAL"] = "владелец лечилки решает, спасать ли умирающего"
    router.message.outer_middleware(DndManualHealingMiddleware())
    router.callback_query.outer_middleware(DndManualHealingMiddleware())
    router.callback_query.register(handle_decision_callback, F.data.startswith("dnd:heal:"))
    router._upupa_dnd_healing_installed = True


__all__ = [
    "DECISION_KIND", "DndManualHealingMiddleware", "HEAL_RULE", "apply_decision", "apply_manual_heal",
    "available_charge", "available_count", "begin_dying", "charge_count", "charges_text", "deal_charges",
    "decision_text", "ensure_charges", "enter_decision", "install_dnd_healing", "keyboard", "owned_charge",
    "status_line", "strip_healing_poll_options", "wounded_targets",
]
