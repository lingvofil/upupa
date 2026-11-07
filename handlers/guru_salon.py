"""Салон «Гуру» (эксперимент лаборатории ``guru``): тонкий Telegram-слой.

Правила игры — в ``games.guru``. Здесь только команды, кнопки, таймаут и журнал.

Все хэндлеры стоят за ``ExperimentEnabled("guru")``: в чате, где эксперимент
выключен, «салон» и кнопки уходят дальше по роутерам, как будто салона нет.

Одна партия на чат, один игрок на партию: кнопки жмёт только он, остальные
смотрят (их нажатия пишутся в журнал как ``spectator``). Партия без ходов
``IDLE_TIMEOUT_S`` секунд бросается фоновым циклом ``guru_salon_timeout_loop``,
который запускает composition root через ``TaskSupervisor``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.settings import ADMIN_ID
from features.lab.events import log_event, new_session_id
from features.lab.filters import ExperimentEnabled
from games.guru import engine, render

logger = logging.getLogger(__name__)

EXPERIMENT = "guru"
START_COMMANDS = frozenset({"салон"})
STOP_COMMANDS = frozenset({"салон стоп", "салон закрыть"})
SWEEP_INTERVAL_S = 15
MAX_NAME_LENGTH = 24

router = Router(name="guru_salon")
_enabled = ExperimentEnabled(EXPERIMENT)

_registry = engine.SalonRegistry()
_rng = random.Random()


def _clock() -> float:
    return time.monotonic()


# --- разбор команд ----------------------------------------------------------

def _normalize(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _is_start_command(message: types.Message) -> bool:
    return _normalize(message.text) in START_COMMANDS


def _is_stop_command(message: types.Message) -> bool:
    return _normalize(message.text) in STOP_COMMANDS


def _player_name(user: Any) -> str:
    name = (getattr(user, "first_name", None) or getattr(user, "username", None) or "Аноним").strip()
    return name[:MAX_NAME_LENGTH] or "Аноним"


def _thread_id(message: Any) -> int | None:
    return getattr(message, "message_thread_id", None) if getattr(message, "is_topic_message", False) else None


def _markup(rows: list[list[tuple[str, str]]] | None) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows]
    )


# --- Telegram I/O -----------------------------------------------------------

async def _send(bot: Any, salon: engine.Salon, text: str, rows=None, *, reply_to: int | None = None) -> Any:
    return await bot.send_message(
        salon.chat_id,
        text,
        message_thread_id=salon.thread_id,
        reply_markup=_markup(rows),
        reply_to_message_id=reply_to,
    )


async def _edit(bot: Any, chat_id: int, message_id: int | None, text: str, rows=None) -> None:
    if message_id is None:
        return
    try:
        await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=_markup(rows))
    except TelegramBadRequest as error:
        if "not modified" not in str(error).lower():
            logger.warning("[guru] не удалось обновить сообщение салона: %s", error)
    except TelegramAPIError as error:
        logger.warning("[guru] не удалось обновить сообщение салона: %s", error)


async def _send_client(bot: Any, salon: engine.Salon) -> None:
    sent = await _send(
        bot,
        salon,
        render.render_client(salon, salon.client, client_no=salon.client_no),
        render.client_keyboard(salon),
    )
    salon.message_id = getattr(sent, "message_id", None)


# --- жизненный цикл партии --------------------------------------------------

def _detach(salon: engine.Salon, reason: str, now: float) -> dict[str, Any]:
    """Синхронно убрать партию из реестра и зафиксировать итог (до любых await)."""
    idle_s = round(max(0.0, now - salon.last_activity), 1)
    _registry.remove(salon)
    engine.abandon_salon(salon, reason)
    summary = engine.salon_summary(salon, now)
    summary.update(idle_s=idle_s, client_no=salon.client_no, stuck_client=salon.client.kind.key)
    return summary


async def _log_abandon(salon: engine.Salon, summary: dict[str, Any]) -> None:
    await log_event(
        EXPERIMENT,
        "abandon",
        chat_id=salon.chat_id,
        user_id=salon.player_id,
        session_id=salon.session_id,
        **summary,
    )


async def _show_abandoned(bot: Any, salon: engine.Salon) -> None:
    await _edit(bot, salon.chat_id, salon.message_id, render.render_abandoned_client(salon))
    try:
        await _send(bot, salon, render.render_summary(salon), render.again_keyboard(), reply_to=salon.message_id)
    except TelegramAPIError as error:
        logger.warning("[guru] не удалось отправить итог брошенного салона: %s", error)


async def _start_salon(bot: Any, chat_id: int, user: Any, thread_id: int | None, *, via: str) -> None:
    now = _clock()
    current = _registry.get(chat_id)
    expired: tuple[engine.Salon, dict[str, Any]] | None = None
    if current is not None and engine.is_idle(current, now):
        # Таймаут уже наступил, но цикл ещё не успел его забрать.
        expired = (current, _detach(current, "timeout", now))
        current = None

    if current is not None:
        await log_event(
            EXPERIMENT,
            "start_rejected",
            chat_id=chat_id,
            user_id=user.id,
            session_id=current.session_id,
            reason="busy",
            own=current.player_id == user.id,
            via=via,
        )
        if current.player_id == user.id:
            text = "Ты уже принимаешь клиента — жми кнопки выше. Бросить партию: «салон стоп»."
        else:
            text = f"Салон занят: принимает мадам {current.player_name}. Дождись, пока закроется."
        await bot.send_message(chat_id, text, message_thread_id=thread_id)
        return

    salon = engine.new_salon(
        session_id=new_session_id(),
        chat_id=chat_id,
        player_id=user.id,
        player_name=_player_name(user),
        rng=_rng,
        now=now,
    )
    salon.thread_id = thread_id
    # В реестр — до первого await: второй «салон» в ту же секунду увидит занятый чат.
    _registry.add(salon)

    if expired is not None:
        old, summary = expired
        await _log_abandon(old, summary)
        await _edit(bot, old.chat_id, old.message_id, render.render_abandoned_client(old))

    await log_event(
        EXPERIMENT,
        "start",
        chat_id=chat_id,
        user_id=user.id,
        session_id=salon.session_id,
        via=via,
        clients=list(salon.plan),
        rules=engine.RULES_VERSION,
    )
    try:
        await _send(bot, salon, render.render_rules(salon))
        await _send_client(bot, salon)
    except TelegramAPIError:
        logger.exception("[guru] салон не открылся: Telegram не принял сообщение")
        if _registry.get(chat_id) is salon:
            await _log_abandon(salon, _detach(salon, "send_failed", _clock()))


# --- хэндлеры ---------------------------------------------------------------

@router.message(_enabled, _is_start_command)
async def start_salon(message: types.Message) -> None:
    await _start_salon(message.bot, message.chat.id, message.from_user, _thread_id(message), via="command")


@router.message(_enabled, _is_stop_command)
async def stop_salon(message: types.Message) -> None:
    salon = _registry.get(message.chat.id)
    if salon is None:
        await message.reply("Салон и так закрыт. Открыть: «салон».")
        return
    if message.from_user.id not in (salon.player_id, ADMIN_ID):
        await message.reply(f"Закрыть салон может только мадам {salon.player_name}.")
        return
    summary = _detach(salon, "quit", _clock())
    await _log_abandon(salon, summary)
    await _show_abandoned(message.bot, salon)


@router.callback_query(_enabled, F.data.startswith(render.CALLBACK_PREFIX))
async def salon_callback(callback: types.CallbackQuery) -> None:
    parsed = render.parse_callback(callback.data)
    message = callback.message
    if parsed is None or message is None:
        await callback.answer()
        return
    chat_id = message.chat.id

    if parsed.action == "again":
        await callback.answer()
        await _start_salon(callback.bot, chat_id, callback.from_user, _thread_id(message), via="replay")
        return

    salon = _registry.get(chat_id)
    if salon is None or not render.is_current(salon, parsed):
        await callback.answer("Эта кнопка уже не работает.")
        return
    if callback.from_user.id != salon.player_id:
        await log_event(
            EXPERIMENT,
            "spectator",
            chat_id=chat_id,
            user_id=callback.from_user.id,
            session_id=salon.session_id,
            move=parsed.move,
        )
        await callback.answer(f"Это салон мадам {salon.player_name}. Смотри и учись.")
        return

    # Ход применяется синхронно, до первого await: повторное нажатие той же
    # кнопки уже не пройдёт проверку is_current.
    now = _clock()
    result = engine.apply_move(salon, parsed.move, _rng, now=now)
    if salon.finished:
        _registry.remove(salon)
    await callback.answer()
    await log_event(
        EXPERIMENT,
        "action",
        chat_id=chat_id,
        user_id=salon.player_id,
        session_id=salon.session_id,
        **result.log_data(),
    )

    bot = callback.bot
    message_id = message.message_id
    if result.finished_client is None:
        await _edit(
            bot,
            chat_id,
            message_id,
            render.render_client(salon, salon.client, client_no=salon.client_no),
            render.client_keyboard(salon),
        )
        return

    await _edit(bot, chat_id, message_id, render.render_client(salon, result.finished_client, client_no=result.client_no))
    if salon.finished:
        await log_event(
            EXPERIMENT,
            "finish",
            chat_id=chat_id,
            user_id=salon.player_id,
            session_id=salon.session_id,
            **engine.salon_summary(salon, now),
        )
        await _send(bot, salon, render.render_summary(salon), render.again_keyboard())
        return
    await _send_client(bot, salon)


# --- таймаут ----------------------------------------------------------------

async def sweep_idle_salons(bot: Any, *, now: float | None = None) -> int:
    """Бросить партии без ходов дольше таймаута. Возвращает, сколько бросили."""
    moment = _clock() if now is None else now
    idle = _registry.idle(moment)
    for salon in idle:
        summary = _detach(salon, "timeout", moment)
        await _log_abandon(salon, summary)
        await _show_abandoned(bot, salon)
    return len(idle)


async def _abandon_all(reason: str) -> None:
    now = _clock()
    for salon in _registry.active():
        await _log_abandon(salon, _detach(salon, reason, now))


async def guru_salon_timeout_loop(bot: Any, *, interval: float = SWEEP_INTERVAL_S) -> None:
    """Фоновый цикл салона. При остановке бота записывает брошенные партии в журнал."""
    try:
        while True:
            await asyncio.sleep(interval)
            await sweep_idle_salons(bot)
    except asyncio.CancelledError:
        # Партии живут в памяти: без этой записи итог прерванной партии пропадёт.
        await _abandon_all("restart")
        raise
