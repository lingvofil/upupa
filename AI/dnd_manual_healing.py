"""Manual, player-controlled DnD healing outside ordinary story polls."""
from __future__ import annotations

import re

from aiogram import BaseMiddleware
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


MANUAL_HEAL_RULE = (
    "Аварийные лечилки — отдельная механика игрока. Владелец может в любой момент во время партии "
    "написать команду «лечить» и выбрать живого раненого героя. НЕ предлагай лечение, аптечку, хил или "
    "расход лечилки как вариант обычного ACTION:POLL: бот показывает лечение отдельно по команде владельца. "
    "При падении до 0 HP сохраняется отдельный аварийный выбор спасения."
)

_POLL_ACTION_RE = re.compile(r"\[ACTION:(POLL;[^\]]*?OPTIONS:[^\]]+)\]", re.I | re.S)
_HEAL_OPTION_RE = re.compile(
    r"(?:\bлеч\w*|\bвылеч\w*|\bхил\w*|\bаптеч\w*|\bлечил\w*|"
    r"восстанов\w*\s+(?:hp|хп|здоров\w*))",
    re.I,
)
_TARGETS_RE = re.compile(r"(?:^|;)TARGETS:([0-9,]+)(?:;|$)", re.I)


def _is_heal_option(value: str) -> bool:
    return bool(_HEAL_OPTION_RE.search(str(value or "")))


def strip_healing_poll_options(response: str) -> str:
    """Remove healing choices from model polls; healing has its own command/UI."""
    text = str(response or "")

    def replace(match: re.Match) -> str:
        command = match.group(1)
        marker = command.upper().find("OPTIONS:")
        if marker < 0:
            return match.group(0)
        prefix = command[: marker + len("OPTIONS:")]
        raw_options = command[marker + len("OPTIONS:") :]
        options = [item.strip() for item in raw_options.split(";") if item.strip()]
        kept = [item for item in options if not _is_heal_option(item)]
        if len(kept) == len(options):
            return match.group(0)
        if len(kept) >= 2:
            return f"[ACTION:{prefix}{';'.join(kept)}]"
        targets = _TARGETS_RE.search(command)
        if targets:
            return f"[ACTION:INPUT;TARGETS:{targets.group(1)}]"
        return "[ACTION:INPUT]"

    return _POLL_ACTION_RE.sub(replace, text)


def _owned_charge(two_heals, session, owner_id: int) -> dict | None:
    for charge in two_heals._ensure_charges(session):
        if charge.get("used") or charge.get("lost"):
            continue
        if int(charge.get("owner_id", -1)) == int(owner_id):
            return charge
    return None


def _wounded_targets(session) -> list[tuple[int, int, int]]:
    rows = []
    for raw_id, sheet in (getattr(session, "character_sheets", {}) or {}).items():
        if not isinstance(sheet, dict):
            continue
        try:
            user_id = int(raw_id)
            hp = int(sheet.get("hp", 0))
            max_hp = int(sheet.get("max_hp", 0))
        except (TypeError, ValueError):
            continue
        status = str(sheet.get("status") or "alive").casefold()
        if status == "alive" and 0 < hp < max_hp:
            rows.append((user_id, hp, max_hp))
    return rows


def _apply_manual_heal(two_heals, combat, session, owner_id: int, charge_id: int, target_id: int, *, roll: int | None = None):
    charge = two_heals._charge_by_id(session, int(charge_id))
    if not charge or charge.get("used") or charge.get("lost"):
        return False, "Эта лечилка уже недоступна."
    if int(charge.get("owner_id", -1)) != int(owner_id):
        return False, "Это не твоя лечилка."
    if not two_heals._owner_can_use(session, int(owner_id), None):
        return False, "Сейчас ты не можешь использовать лечилку."

    sheet = (getattr(session, "character_sheets", {}) or {}).get(str(int(target_id)))
    if not isinstance(sheet, dict):
        return False, "Такого героя сейчас нет в партии."
    try:
        hp = int(sheet.get("hp", 0))
        max_hp = int(sheet.get("max_hp", 0))
    except (TypeError, ValueError):
        return False, "У героя сломано состояние здоровья."
    if str(sheet.get("status") or "alive").casefold() != "alive" or hp <= 0:
        return False, "Герой при смерти или погиб — тут работает аварийное спасение, а не команда «лечить»."
    if hp >= max_hp:
        return False, "У героя и так полное здоровье."

    amount = int(roll) if roll is not None else two_heals.random.randint(1, 8) + 2
    amount = max(1, amount)
    new_hp = min(max_hp, hp + amount)
    restored = new_hp - hp
    sheet["hp"] = new_hp
    charge["used"] = True
    two_heals._sync_legacy_charge(session)
    owner_name = combat._participant_name(session, int(owner_id))
    target_name = combat._participant_name(session, int(target_id))
    return True, (
        f"🧪 {owner_name} тратит лечилку на {target_name}: +{restored} HP. "
        f"❤️ {target_name}: {new_hp}/{max_hp}. Лечилка израсходована."
    )


def _heal_keyboard(combat, session, owner_id: int, charge_id: int) -> InlineKeyboardMarkup:
    rows = []
    for target_id, hp, max_hp in _wounded_targets(session):
        rows.append([
            InlineKeyboardButton(
                text=f"{combat._participant_name(session, target_id)} · {hp}/{max_hp} HP",
                callback_data=f"dnd:manual_heal:{owner_id}:{charge_id}:{target_id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class DndManualHealingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        from AI import dnd
        from AI import dnd_combat as combat
        from AI import dnd_two_heals as two_heals

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
            ok, text = _apply_manual_heal(two_heals, combat, session, owner_id, charge_id, target_id)
            if not ok:
                await event.answer(text, show_alert=True)
                return None
            dnd.persist_dnd_sessions()
            await event.answer("Подлечили.")
            if message is not None:
                await message.edit_text(text)
            return None

        text = str(getattr(event, "text", None) or "").strip().casefold()
        if text != "лечить":
            return await handler(event, data)
        chat = getattr(event, "chat", None)
        actor = getattr(event, "from_user", None)
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
        charge = _owned_charge(two_heals, session, owner_id)
        if not charge:
            await event.answer("У тебя нет доступной лечилки.")
            return None
        targets = _wounded_targets(session)
        if not targets:
            await event.answer("Все живые герои и так с полным здоровьем — лечилку пока не тратим.")
            return None
        await event.answer(
            "🧪 Кого лечить? Лечилка одноразовая.",
            reply_markup=_heal_keyboard(combat, session, owner_id, int(charge["id"])),
        )
        return None


def install_dnd_manual_healing(router) -> None:
    from AI import dnd

    if getattr(router, "_upupa_dnd_manual_healing_installed", False):
        return
    router.message.outer_middleware(DndManualHealingMiddleware())
    router.callback_query.outer_middleware(DndManualHealingMiddleware())

    if MANUAL_HEAL_RULE not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + MANUAL_HEAL_RULE

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if session and dnd._is_participant_mode(session):
            response = strip_healing_poll_options(response)
        return await original_parse(bot, chat_id, response)

    dnd.parse_and_execute_turn = parse_turn
    router._upupa_dnd_manual_healing_installed = True


__all__ = [
    "MANUAL_HEAL_RULE",
    "DndManualHealingMiddleware",
    "install_dnd_manual_healing",
    "strip_healing_poll_options",
    "_apply_manual_heal",
    "_wounded_targets",
]
