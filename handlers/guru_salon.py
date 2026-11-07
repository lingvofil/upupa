"""Салон «Гуру» (эксперимент лаборатории ``guru``): тонкий Telegram-слой.

Правила — в ``games.guru.rules``, текст и кнопки — в ``games.guru.textui``.
Здесь только команды, callback, таймаут и запись метрик ядра в журнал.

Все хэндлеры стоят за ``ExperimentEnabled("guru")``: в чате, где эксперимент
выключен, «гуру лаб» и кнопки уходят дальше по роутерам, как будто салона нет.
В личке с ботом эксперимент включается так же: ``лаба вкл гуру``.

Одна партия на чат, один игрок на партию: кнопки жмёт только он, нажатия
зрителей пишутся как ``spectator``. Партия без ходов ``IDLE_TIMEOUT_S`` секунд
бросается фоновым циклом ``guru_salon_timeout_loop`` (запускает composition
root через ``TaskSupervisor``).
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
from games.guru import rules, textui
from games.guru.model import Session

logger = logging.getLogger(__name__)

EXPERIMENT = "guru"
START_COMMANDS = frozenset({"гуру лаб"})
STOP_COMMANDS = frozenset({"гуру лаб стоп"})
SWEEP_INTERVAL_S = 15
MAX_NAME_LENGTH = 24

router = Router(name="guru_salon")
_enabled = ExperimentEnabled(EXPERIMENT)

_registry = rules.SessionRegistry()
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


def _markup(rows: textui.Rows | None) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows]
    )


# --- Telegram I/O -----------------------------------------------------------

async def _send(bot: Any, session: Session, text: str, rows: textui.Rows | None = None, *,
                reply_to: int | None = None) -> Any:
    return await bot.send_message(
        session.chat_id,
        text,
        message_thread_id=session.thread_id,
        reply_markup=_markup(rows),
        reply_to_message_id=reply_to,
    )


async def _edit(bot: Any, chat_id: int, message_id: int | None, text: str, rows: textui.Rows | None = None) -> None:
    if message_id is None:
        return
    try:
        await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=_markup(rows))
    except TelegramBadRequest as error:
        if "not modified" not in str(error).lower():
            logger.warning("[guru] не удалось обновить сообщение салона: %s", error)
    except TelegramAPIError as error:
        logger.warning("[guru] не удалось обновить сообщение салона: %s", error)


async def _send_client(bot: Any, session: Session) -> None:
    text, rows = textui.render_view(rules.build_view(session))
    sent = await _send(bot, session, text, rows)
    session.message_id = getattr(sent, "message_id", None)


async def _log_metrics(session: Session, metrics: tuple[tuple[str, dict[str, Any]], ...]) -> None:
    for event, data in metrics:
        await log_event(
            EXPERIMENT, event, chat_id=session.chat_id, user_id=session.host.user_id,
            session_id=session.session_id, **data,
        )


# --- жизненный цикл партии --------------------------------------------------

def _detach(session: Session, reason: str, now: float) -> dict[str, Any]:
    """Синхронно убрать партию из реестра и зафиксировать итог (до любых await)."""
    _registry.remove(session)
    return rules.abandon_summary(session, reason, now)


async def _log_abandon(session: Session, summary: dict[str, Any]) -> None:
    await log_event(
        EXPERIMENT, "abandon", chat_id=session.chat_id, user_id=session.host.user_id,
        session_id=session.session_id, **summary,
    )


async def _show_abandoned(bot: Any, session: Session, reason: str, *, summary_message: bool = True) -> None:
    view = rules.build_view(session)
    client = rules.current_client_view(session)
    await _edit(bot, session.chat_id, session.message_id, textui.render_abandoned(view, client, reason))
    if not summary_message:
        return
    text, rows = textui.render_summary(view)
    try:
        await _send(bot, session, text, rows, reply_to=session.message_id)
    except TelegramAPIError as error:
        logger.warning("[guru] не удалось отправить итог брошенного салона: %s", error)


async def _start_session(bot: Any, chat_id: int, user: Any, thread_id: int | None, *, via: str,
                         previous: str | None = None) -> None:
    now = _clock()
    current = _registry.get(chat_id)
    expired: tuple[Session, dict[str, Any]] | None = None
    if current is not None and rules.is_idle(current, now):
        # Таймаут уже наступил, но цикл ещё не успел его забрать.
        expired = (current, _detach(current, "timeout", now))
        current = None

    if current is not None:
        await log_event(
            EXPERIMENT, "start_rejected", chat_id=chat_id, user_id=user.id, session_id=current.session_id,
            reason="busy", own=current.host.user_id == user.id, via=via,
        )
        if current.host.user_id == user.id:
            text = "Ты уже принимаешь клиента — жми кнопки выше. Бросить партию: «гуру лаб стоп»."
        else:
            text = f"Салон занят: принимает мадам {current.host.name}. Дождись, пока закроется."
        await bot.send_message(chat_id, text, message_thread_id=thread_id)
        return

    session = rules.new_session(
        session_id=new_session_id(),
        chat_id=chat_id,
        player_id=user.id,
        player_name=_player_name(user),
        rng=_rng,
        now=now,
    )
    session.thread_id = thread_id
    # В реестр — до первого await: второй старт в ту же секунду увидит занятый чат.
    _registry.add(session)

    if expired is not None:
        old, summary = expired
        await _log_abandon(old, summary)
        await _show_abandoned(bot, old, "timeout", summary_message=False)

    await log_event(
        EXPERIMENT, "start", chat_id=chat_id, user_id=user.id, session_id=session.session_id,
        via=via, previous=previous, **rules.start_data(session),
    )
    try:
        await _send(bot, session, textui.render_intro(rules.build_view(session)))
        await _send_client(bot, session)
    except TelegramAPIError:
        logger.exception("[guru] салон не открылся: Telegram не принял сообщение")
        if _registry.get(chat_id) is session:
            await _log_abandon(session, _detach(session, "send_failed", _clock()))


# --- хэндлеры ---------------------------------------------------------------

@router.message(_enabled, _is_start_command)
async def start_salon(message: types.Message) -> None:
    await _start_session(message.bot, message.chat.id, message.from_user, _thread_id(message), via="command")


@router.message(_enabled, _is_stop_command)
async def stop_salon(message: types.Message) -> None:
    session = _registry.get(message.chat.id)
    if session is None:
        await message.reply("Салон и так закрыт. Открыть: «гуру лаб».")
        return
    if message.from_user.id != ADMIN_ID and not session.is_participant(message.from_user.id):
        await message.reply(f"Закрыть салон может только мадам {session.host.name}.")
        return
    summary = _detach(session, "quit", _clock())
    await _log_abandon(session, summary)
    await _show_abandoned(message.bot, session, "quit")


async def _again(callback: types.CallbackQuery, parsed: textui.Callback) -> None:
    message = callback.message
    chat_id = message.chat.id
    await log_event(
        EXPERIMENT, "replay_answer", chat_id=chat_id, user_id=callback.from_user.id,
        session_id=parsed.session_id, answer=parsed.answer,
    )
    if parsed.answer == "yes":
        await callback.answer()
        await _start_session(callback.bot, chat_id, callback.from_user, _thread_id(message), via="replay",
                             previous=parsed.session_id)
        return
    await callback.answer("Салон закрыт до лучших времён.")
    try:
        await callback.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message.message_id,
                                                     reply_markup=None)
    except TelegramAPIError as error:
        logger.warning("[guru] не удалось убрать кнопки итога: %s", error)


@router.callback_query(_enabled, F.data.startswith(textui.CALLBACK_PREFIX))
async def salon_callback(callback: types.CallbackQuery) -> None:
    parsed = textui.parse(callback.data)
    message = callback.message
    if parsed is None or message is None:
        await callback.answer()
        return
    if parsed.kind == "again":
        await _again(callback, parsed)
        return

    chat_id = message.chat.id
    session = _registry.get(chat_id)
    if session is None or session.session_id != parsed.session_id or session.step != parsed.step:
        await callback.answer("Эта кнопка уже не работает.")
        return
    if not session.is_participant(callback.from_user.id):
        await log_event(
            EXPERIMENT, "spectator", chat_id=chat_id, user_id=callback.from_user.id,
            session_id=session.session_id, action=parsed.action,
        )
        await callback.answer(f"Это салон мадам {session.host.name}. Смотри и учись.")
        return

    # Ход применяется синхронно, до первого await: повторное нажатие той же
    # кнопки уже не совпадёт по номеру шага.
    try:
        result = rules.act(session, callback.from_user.id, parsed.action or "", _rng, now=_clock())
    except (ValueError, PermissionError):
        await callback.answer("Эта кнопка уже не работает.")
        return
    if session.finished:
        _registry.remove(session)
    await callback.answer()
    await _log_metrics(session, result.metrics)

    bot = callback.bot
    message_id = message.message_id
    if result.finished_client is None:
        text, rows = textui.render_view(result.view)
        await _edit(bot, chat_id, message_id, text, rows)
        return

    await _edit(bot, chat_id, message_id, textui.render_finished(result.view, result.finished_client))
    if session.finished:
        text, rows = textui.render_summary(result.view)
        await _send(bot, session, text, rows)
        return
    await _send_client(bot, session)


# --- таймаут ----------------------------------------------------------------

async def sweep_idle_salons(bot: Any, *, now: float | None = None) -> int:
    """Бросить партии без ходов дольше таймаута. Возвращает, сколько бросили."""
    moment = _clock() if now is None else now
    idle = _registry.idle(moment)
    for session in idle:
        summary = _detach(session, "timeout", moment)
        await _log_abandon(session, summary)
        await _show_abandoned(bot, session, "timeout")
    return len(idle)


async def _abandon_all(reason: str) -> None:
    now = _clock()
    for session in _registry.active():
        await _log_abandon(session, _detach(session, reason, now))


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
