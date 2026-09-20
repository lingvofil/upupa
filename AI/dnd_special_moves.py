"""One-use player-confirmed special moves for participant DnD."""
from __future__ import annotations

import re

from aiogram import BaseMiddleware
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


SPECIAL_MARKER = "ОСОБЫЕ ПРИЁМЫ DND УПУПЫ"
SPECIAL_RULES = f"""
{SPECIAL_MARKER}.
У каждого героя один заряд выбранного при создании «особого приёма» на приключение.
Если особый приём действительно подходит к предстоящей проверке, добавь SPECIAL:1 в
ACTION:ROLL, ACTION:PLAYER_ATTACK или ACTION:CINEMATIC_ATTACK. Код предложит владельцу подтвердить применение.
После подтверждения приём даёт преимущество; если уже была помеха, преимущество и помеха взаимно отменяются.
Не давай за базовый особый приём одновременно ещё и повышение исхода, автоуспех или дополнительный урон.
Заряд расходуется только после подтверждения владельца и фактического броска, в котором бонус уже был использован.
Если приём неприменим или бросок не состоялся, заряд остаётся. Сбой сюжетной генерации ПОСЛЕ броска не возвращает заряд.
""".strip()

_ACTION_RE = re.compile(r"\[ACTION:([A-Z_]+)([^\]]*)\]", re.I | re.S)
_TARGETS_RE = re.compile(r"(?:^|;)TARGETS:([0-9,\s]+)(?=;|$)", re.I)
_SPECIAL_RE = re.compile(r"(?:^|;)SPECIAL:(?:1|TRUE|YES)(?=;|$)", re.I)
_PHRASES = {
    "использую приём", "использую прием",
    "использую особый приём", "использую особый прием",
}


def _clean(value, limit=100) -> str:
    return " ".join(str(value or "").replace("[", "").replace("]", "").split()).strip(" ;")[:limit]


def _participant_ids(session) -> set[str]:
    result = set()
    for key, row in (getattr(session, "participants", {}) or {}).items():
        try:
            result.add(str(int(row.get("user_id", key))))
        except (TypeError, ValueError):
            continue
    return result


def _profile_special(session, user_id: int) -> str:
    profile = (getattr(session, "character_profiles", {}) or {}).get(str(int(user_id))) or {}
    return _clean(profile.get("special"), 100)


def _ensure(session) -> None:
    raw_charges = getattr(session, "special_move_charges", None)
    normalized = {}
    for key, value in (raw_charges.items() if isinstance(raw_charges, dict) else []):
        try:
            normalized[str(int(key))] = 1 if int(value) > 0 else 0
        except (TypeError, ValueError):
            continue
    session.special_move_charges = normalized
    for key in _participant_ids(session):
        if _profile_special(session, int(key)) and key not in session.special_move_charges:
            session.special_move_charges[key] = 1
    for field in ("special_move_pending_user_id", "special_move_offer_user_id"):
        raw = getattr(session, field, None)
        try:
            setattr(session, field, int(raw) if raw is not None else None)
        except (TypeError, ValueError):
            setattr(session, field, None)


def _restore(session, data) -> None:
    row = data if isinstance(data, dict) else {}
    session.special_move_charges = {
        str(key): int(value)
        for key, value in (row.get("special_move_charges") or {}).items()
        if str(value).lstrip("-").isdigit()
    }
    session.special_move_pending_user_id = row.get("special_move_pending_user_id")
    session.special_move_offer_user_id = row.get("special_move_offer_user_id")
    _ensure(session)


def _targets(suffix: str) -> list[int]:
    match = _TARGETS_RE.search(";" + str(suffix or "").strip(";") + ";")
    if not match:
        return []
    return [int(value) for value in match.group(1).split(",") if value.strip().isdigit()]


def special_eligible_user(response: str) -> int | None:
    match = _ACTION_RE.search(str(response or ""))
    if not match or match.group(1).upper() not in {"ROLL", "PLAYER_ATTACK", "CINEMATIC_ATTACK"}:
        return None
    suffix = match.group(2) or ""
    if not _SPECIAL_RE.search(";" + suffix.strip(";") + ";"):
        return None
    targets = _targets(suffix)
    return targets[0] if len(targets) == 1 else None


def _available(session, user_id: int) -> bool:
    _ensure(session)
    return bool(_profile_special(session, user_id)) and int(
        session.special_move_charges.get(str(int(user_id)), 0) or 0
    ) > 0


def _combine_advantage(mode: str) -> str:
    mode = str(mode or "NORMAL").upper()
    if mode == "DISADVANTAGE":
        return "NORMAL"
    return "ADVANTAGE"


def activate_special(session, user_id: int) -> tuple[bool, str]:
    _ensure(session)
    pending = getattr(session, "pending_roll", None)
    if getattr(session, "state", None) != "WAITING_ROLL" or not isinstance(pending, dict):
        return False, "Сейчас нет броска, к которому можно применить особый приём."
    if int(getattr(session, "special_move_offer_user_id", -1) or -1) != int(user_id):
        return False, "К этой проверке особый приём сейчас не подходит."
    targets = [int(value) for value in (pending.get("target_user_ids") or [])]
    if targets and int(user_id) not in targets:
        return False, "Это не твой бросок."
    if not _available(session, user_id):
        return False, "Заряд особого приёма уже потрачен."
    mode = str(pending.get("mode") or "NORMAL").upper()
    if mode == "ADVANTAGE":
        return False, "На броске уже есть преимущество — заряд не расходуем."
    pending["mode"] = _combine_advantage(mode)
    session.special_move_pending_user_id = int(user_id)
    session.special_move_offer_user_id = None
    return True, f"🔥 Особый приём «{_profile_special(session, user_id)}» активирован. Теперь пиши «кидаю»."


def _commit_completed_special_roll(session, pending_roll, user_id) -> bool:
    if user_id is None or not isinstance(pending_roll, dict):
        return False
    _ensure(session)
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return False
    targets = [int(value) for value in (pending_roll.get("target_user_ids") or [])]
    if targets and user_id not in targets:
        return False
    # The same pending-roll object still being active means no roll was actually
    # consumed (wrong actor, rejected command, etc.).
    if getattr(session, "pending_roll", None) is pending_roll:
        return False
    key = str(user_id)
    if int(session.special_move_charges.get(key, 0) or 0) <= 0:
        session.special_move_pending_user_id = None
        session.special_move_offer_user_id = None
        return False
    session.special_move_charges[key] = 0
    session.special_move_pending_user_id = None
    session.special_move_offer_user_id = None
    return True


def _offer_text(session, user_id: int) -> str:
    mode = str((getattr(session, "pending_roll", {}) or {}).get("mode") or "NORMAL").upper()
    effect = "снимет помеху и вернёт обычный бросок" if mode == "DISADVANTAGE" else "даст преимущество"
    return f"🔥 Особый приём «{_profile_special(session, user_id)}» доступен: {effect}. Заряд 1/1."


def _keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔥 Использовать приём", callback_data=f"dnd:special:{int(user_id)}")
    ]])


def _context(session) -> str:
    _ensure(session)
    lines = []
    for key in _participant_ids(session):
        special = _profile_special(session, int(key))
        if not special:
            continue
        person = (getattr(session, "participants", {}) or {}).get(key, {})
        who = person.get("name") or f"ID {key}"
        charge = int(session.special_move_charges.get(key, 0) or 0)
        lines.append(f"- {who}: «{special}», заряд {charge}/1")
    return SPECIAL_RULES + (("\nОСОБЫЕ ПРИЁМЫ:\n" + "\n".join(lines)) if lines else "")


class SpecialMoveMessageMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        text = " ".join(str(getattr(event, "text", "") or "").strip().casefold().split())
        if text not in _PHRASES:
            return await handler(event, data)
        from AI import dnd
        chat = getattr(event, "chat", None)
        user = getattr(event, "from_user", None)
        if chat is None or user is None or not hasattr(event, "answer"):
            return await handler(event, data)
        session = dnd.dnd_sessions.get(int(chat.id))
        if not session or not dnd._is_participant_mode(session):
            return await handler(event, data)
        ok, message = activate_special(session, int(user.id))
        dnd.persist_dnd_sessions()
        await event.answer(message)
        return None


class SpecialMoveCallbackMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        value = str(getattr(event, "data", "") or "")
        if not value.startswith("dnd:special:"):
            return await handler(event, data)
        from AI import dnd
        try:
            target_id = int(value.rsplit(":", 1)[1])
        except (TypeError, ValueError):
            await event.answer("Кнопка протухла.", show_alert=True)
            return None
        user = getattr(event, "from_user", None)
        message = getattr(event, "message", None)
        chat = getattr(message, "chat", None)
        if user is None or chat is None:
            return None
        if int(user.id) != target_id:
            await event.answer("Это чужой особый приём.", show_alert=True)
            return None
        session = dnd.dnd_sessions.get(int(chat.id))
        if not session:
            await event.answer("Егра уже закончилась.", show_alert=True)
            return None
        ok, reply = activate_special(session, int(user.id))
        dnd.persist_dnd_sessions()
        await event.answer("Готово." if ok else reply, show_alert=not ok)
        if ok and message is not None:
            await message.answer(reply)
        return None


def install_dnd_special_moves(dnd, dnd_router, *, state_policy) -> None:
    from AI import dnd_campaign as campaign
    from AI import dnd_combat as combat

    if getattr(dnd, "_upupa_dnd_special_moves_installed", False):
        return

    state_policy.add_ensure_hook(_ensure)
    state_policy.add_state_field("special_move_charges", lambda session: dict(getattr(session, "special_move_charges", {}) or {}))
    state_policy.add_state_field("special_move_pending_user_id", lambda session: getattr(session, "special_move_pending_user_id", None))
    state_policy.add_state_field("special_move_offer_user_id", lambda session: getattr(session, "special_move_offer_user_id", None))
    state_policy.add_restore_hook(_restore)

    if SPECIAL_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + SPECIAL_RULES

    original_context = campaign._campaign_context
    campaign._campaign_context = lambda dnd_module, session: original_context(dnd_module, session) + "\n" + _context(session)

    original_open_action = dnd.open_action_window

    async def open_action_window(bot, chat_id, target_user_ids=None):
        session = dnd.dnd_sessions.get(chat_id)
        if session:
            session.special_move_pending_user_id = None
            session.special_move_offer_user_id = None
        return await original_open_action(bot, chat_id, target_user_ids=target_user_ids)

    dnd.open_action_window = open_action_window

    original_resolve_player_roll = combat._resolve_player_roll

    async def resolve_player_roll(dnd_module, message, session):
        pending_roll = getattr(session, "pending_roll", None)
        pending_user = getattr(session, "special_move_pending_user_id", None)
        try:
            return await original_resolve_player_roll(dnd_module, message, session)
        finally:
            if _commit_completed_special_roll(session, pending_roll, pending_user):
                dnd_module.persist_dnd_sessions()

    combat._resolve_player_roll = resolve_player_roll

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        eligible = special_eligible_user(response) if session and dnd._is_participant_mode(session) else None
        result = await original_parse(bot, chat_id, response)
        if session and eligible is not None and getattr(session, "state", None) == "WAITING_ROLL" and _available(session, eligible):
            pending = getattr(session, "pending_roll", None) or {}
            targets = [int(value) for value in (pending.get("target_user_ids") or [])]
            if not targets or int(eligible) in targets:
                session.special_move_offer_user_id = int(eligible)
                dnd.persist_dnd_sessions()
                await bot.send_message(chat_id, _offer_text(session, eligible), reply_markup=_keyboard(eligible))
        return result

    dnd.parse_and_execute_turn = parse_turn

    dnd_router.message.outer_middleware(SpecialMoveMessageMiddleware())
    dnd_router.callback_query.outer_middleware(SpecialMoveCallbackMiddleware())
    dnd._upupa_dnd_special_moves_installed = True


__all__ = [
    "SPECIAL_MARKER", "SPECIAL_RULES",
    "special_eligible_user", "activate_special", "_commit_completed_special_roll",
    "install_dnd_special_moves",
]
