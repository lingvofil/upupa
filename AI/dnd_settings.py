"""Persistent chat-level DnD pacing and turn deadlines."""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import BaseMiddleware, F
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.json_repository import JsonFileRepository
from core.paths import DATA_DIR
from core.settings import ADMIN_ID

ALLOWED_CHAT_ID = -1001707530786
# Personal/group frequency is one ratio, so the two controls cannot conflict.
OPTIONS = {
    "personal": ("Личных ходов на общий", (1, 2, 4, 6, 8)),
    "poll": ("Голосование через решений", (0, 4, 6, 8, 12)),
    "monsters": ("Схваток за сюжет", (0, 1, 2, 3)),
    "complexity": ("Сложность сюжета", ("простая", "обычная", "сложная")),
    "turn_seconds": ("Общий ход / голосование, сек", (60, 120, 180, 300, 600)),
    "personal_seconds": ("Пропуск личного хода, сек", (0, 120, 180, 300, 600)),
}
DEFAULTS = dict(personal=4, poll=6, monsters=1, complexity="обычная", turn_seconds=180, personal_seconds=0)
_repository = JsonFileRepository(DATA_DIR / "dnd_settings.json")
_settings = None


def settings_for(session=None):
    global _settings
    if _settings is None:
        try:
            raw = _repository.load()
        except FileNotFoundError:
            raw = {}
        except Exception:
            logging.exception("DnD settings load failed")
            raw = {}
        _settings = {key: raw.get(key, default) if isinstance(raw, dict) and raw.get(key, default) in OPTIONS[key][1]
                     else default for key, default in DEFAULTS.items()}
    return dict(_settings)


def settings_context(session):
    values = settings_for(session)
    return (
        f"НАСТРОЙКИ DND (приоритет над общими рекомендациями): {values['personal']} личных инициатив на один общий INPUT; "
        f"POLL: {'не предлагать' if not values['poll'] else 'примерно через ' + str(values['poll']) + ' решений, только при общей развилке'}. "
        f"Схваток с монстрами за сюжет: {values['monsters']} (новые случайные нападения запрещены при 0; реакция на атаку героя допустима). "
        f"Сложность сюжета: {values['complexity']}. "
        "Частота не отменяет причинность и уже заявленное действие."
    )


def keyboard():
    values = settings_for()
    rows = [[InlineKeyboardButton(text=f"{label}: {values[key] or 'выкл'}", callback_data=f"dnd:settings:{key}")]
            for key, (label, _) in OPTIONS.items()]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def can_edit(dnd, event):
    user_id = event.from_user.id
    session = dnd.dnd_sessions.get(ALLOWED_CHAT_ID)
    if str(user_id) == str(ADMIN_ID) or (session and dnd._user_is_host(session, user_id)):
        return True
    member = await event.bot.get_chat_member(ALLOWED_CHAT_ID, user_id)
    return member.status in {"creator", "administrator"}


class SettingsMiddleware(BaseMiddleware):
    def __init__(self, dnd):
        self.dnd = dnd

    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.chat.id == ALLOWED_CHAT_ID and (event.text or "").strip().casefold() in {"днд настройки", "упупа днд настройки"}:
            await event.answer("⚙️ ДНД: нажмите настройку, чтобы выбрать следующее значение.\n"
                               "Частота партии — один общий ход после указанного числа личных. 0 — выключено.\n"
                               "Менять могут ведущий и администраторы. Новые сроки действуют со следующего хода.", reply_markup=keyboard())
            return
        return await handler(event, data)


class ScopeMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        message = event if isinstance(event, Message) else getattr(event, "message", None)
        if getattr(getattr(message, "chat", None), "id", None) != ALLOWED_CHAT_ID:
            return UNHANDLED
        return await handler(event, data)


async def wait_personal_turn(dnd, bot, session, prompt_id, deadline):
    await asyncio.sleep(max(0, deadline - time.time()))
    if (dnd.dnd_sessions.get(session.chat_id) is not session or session.state != "WAITING_ACTION"
            or session.action_prompt_message_id != prompt_id or session.action_deadline != deadline
            or session.pending_actions or not session.action_target_user_ids):
        return
    from AI.dnd_turn_control import skip_absent_turn
    await skip_absent_turn(dnd, bot, session.chat_id, getattr(session, "starter_user_id", None), automatic=True)


async def wait_personal_roll(dnd, bot, session, roll, deadline):
    await asyncio.sleep(max(0, deadline - time.time()))
    if (dnd.dnd_sessions.get(session.chat_id) is not session or session.state != "WAITING_ROLL"
            or session.pending_roll is not roll or roll.get("personal_deadline") != deadline):
        return
    from AI.dnd_turn_control import skip_absent_turn
    await skip_absent_turn(dnd, bot, session.chat_id, getattr(session, "starter_user_id", None), automatic=True)


def schedule_personal_turn(dnd, bot, session):
    seconds = settings_for(session)["personal_seconds"]
    if seconds and getattr(session, "state", "") == "WAITING_ROLL":
        roll = getattr(session, "pending_roll", None) or {}
        if len(roll.get("target_user_ids") or []) != 1:
            return
        deadline = roll.get("personal_deadline") or time.time() + seconds
        roll["personal_deadline"] = deadline
        dnd.persist_dnd_sessions()
        dnd._start_background_task(wait_personal_roll(dnd, bot, session, roll, deadline),
                                   name=f"dnd-personal-roll:{session.chat_id}:{deadline}")
        return
    if not seconds or len(session.action_target_user_ids) != 1 or not session.action_prompt_message_id:
        return
    deadline = getattr(session, "action_deadline", None) or time.time() + seconds
    session.action_deadline = deadline
    dnd.persist_dnd_sessions()
    dnd._start_background_task(wait_personal_turn(dnd, bot, session, session.action_prompt_message_id, deadline),
                               name=f"dnd-personal:{session.chat_id}:{session.action_prompt_message_id}")


def configure_dnd_settings(dnd, router):
    router.message.outer_middleware(ScopeMiddleware())
    router.callback_query.outer_middleware(ScopeMiddleware())
    router.message.filter(F.chat.id == ALLOWED_CHAT_ID)
    router.callback_query.filter(F.message.chat.id == ALLOWED_CHAT_ID)
    router.message.outer_middleware(SettingsMiddleware(dnd))

    async def change(callback):
        global _settings
        key = callback.data.rsplit(":", 1)[-1]
        if key not in OPTIONS or not await can_edit(dnd, callback):
            await callback.answer("Настройки меняет ведущий или администратор.", show_alert=True)
            return
        updated = settings_for()
        choices = OPTIONS[key][1]
        updated[key] = choices[(choices.index(updated[key]) + 1) % len(choices)]
        _repository.save(updated)
        _settings = updated
        await callback.message.edit_reply_markup(reply_markup=keyboard())
        await callback.answer("Сохранено")

    router.callback_query.register(change, F.data.startswith("dnd:settings:"))


def configure_personal_roll_timer(dnd):
    original = dnd.parse_and_execute_turn

    async def parse(bot, chat_id, response):
        result = await original(bot, chat_id, response)
        session = dnd.dnd_sessions.get(chat_id)
        if session and session.state == "WAITING_ROLL":
            schedule_personal_turn(dnd, bot, session)
        return result

    dnd.parse_and_execute_turn = parse
