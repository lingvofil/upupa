"""Салон «Гуру» (эксперимент лаборатории ``guru``): тонкий Telegram-слой.

Правила — в ``games.guru.rules``, тексты и кнопки — в ``games.guru.textui``.
Здесь только команды, callback, паузы, таймаут и запись метрик ядра в журнал.

Все хэндлеры стоят за ``ExperimentEnabled("guru")``: в чате, где эксперимент
выключен, «гуру» и кнопки уходят дальше по роутерам, как будто салона нет.
В личке с ботом эксперимент включается так же: ``лаба вкл гуру``.

Одна партия на чат: одиночная смена («гуру») или живой приём, где один игрок
гадает другому («гуру приём»). Чужие нажатия пишутся как ``spectator``. Партия
без ходов дольше своего лимита бросается фоновым циклом ``guru_salon_timeout_loop``
(запускает composition root через ``TaskSupervisor``).

Деньги игроков копятся в ``features.guru_wallets``: ядро считает сумму, здесь она
зачисляется, как только приём закончен, поэтому брошенная смена заработанного не теряет.

Паузы — обычный ``asyncio.sleep`` внутри хэндлера: aiogram обрабатывает update
отдельными задачами, поэтому ожидание не задерживает остальных пользователей.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from core.paths import PROJECT_ROOT
from core.settings import ADMIN_ID
from features.guru_wallets import get_guru_wallets
from features.lab.events import log_event, new_session_id
from features.lab.filters import ExperimentEnabled
from games.guru import live, rules, textui
from games.guru.contract import DisplayEvent, LiveView, View
from games.guru.model import ClientReport, LiveSession, Participant, Session

logger = logging.getLogger(__name__)

EXPERIMENT = "guru"
START_COMMANDS = frozenset({"гуру"})
STOP_COMMANDS = frozenset({"гуру стоп"})
LIVE_COMMANDS = frozenset({"гуру приём", "гуру прием"})
WALLET_COMMANDS = frozenset({"гуру касса"})
TAROT_ART_DIR = PROJECT_ROOT / "assets" / "guru_tarot"
SWEEP_INTERVAL_S = 15
MAX_NAME_LENGTH = 24
MAX_CACHED_REPORTS = 300
TYPING_REFRESH_S = 4.0  # индикатор «печатает…» Telegram гаснет примерно через 5 секунд
SUMMARY_PAUSE_S = 3.0
ABANDON_REASONS = frozenset({"timeout", "quit", "restart", "send_failed"})

router = Router(name="guru_salon")
_enabled = ExperimentEnabled(EXPERIMENT)

_registry = rules.SessionRegistry()
_rng = random.Random()
_pace_rng = random.Random()  # разброс пауз не должен влиять на воспроизводимость партии
_reports: dict[str, str] = {}  # подробные разборы до нажатия кнопки; живут в памяти
_card_file_ids: dict[str, str] = {}  # рисунок уже загружен в Telegram: повторно не отправляем файл


def _clock() -> float:
    return time.monotonic()


_sleep = asyncio.sleep


# --- разбор команд ----------------------------------------------------------

def _normalize(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _is_start_command(message: types.Message) -> bool:
    return _normalize(message.text) in START_COMMANDS


def _is_stop_command(message: types.Message) -> bool:
    return _normalize(message.text) in STOP_COMMANDS


def _is_live_command(message: types.Message) -> bool:
    return _normalize(message.text) in LIVE_COMMANDS


def _is_wallet_command(message: types.Message) -> bool:
    return _normalize(message.text) in WALLET_COMMANDS


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

async def _send(bot: Any, session: Session | LiveSession, text: str, rows: textui.Rows | None = None, *,
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


async def _clear_keyboard(bot: Any, chat_id: int, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except TelegramAPIError as error:
        logger.debug("[guru] кнопки уже убраны: %s", error)


async def _send_cards(bot: Any, session: Session | LiveSession, cards: tuple[str, ...]) -> None:
    """Показать расклад альбомом. Если не вышло, игра идёт дальше: текст называет все карты."""
    media = [InputMediaPhoto(media=_card_file_ids.get(art) or FSInputFile(TAROT_ART_DIR / f"{art}.jpg")) for art in cards]
    try:
        sent = await bot.send_media_group(chat_id=session.chat_id, media=media, message_thread_id=session.thread_id)
    except (TelegramAPIError, OSError) as error:
        logger.warning("[guru] не удалось показать расклад: %s", error)
        return
    for art, item in zip(cards, sent):
        if item.photo:
            _card_file_ids[art] = item.photo[-1].file_id


async def _pause(bot: Any, session: Session | LiveSession, seconds: float) -> None:
    """Драматическая пауза: «печатает…» и ожидание. Индикатор — не обязателен."""
    remaining = seconds
    while remaining > 0:
        try:
            await bot.send_chat_action(chat_id=session.chat_id, action="typing", message_thread_id=session.thread_id)
        except TelegramAPIError as error:
            logger.debug("[guru] индикатор набора не показан: %s", error)
        chunk = min(TYPING_REFRESH_S, remaining)
        await _sleep(chunk)
        remaining -= chunk


def _interrupted(session: Session | LiveSession) -> bool:
    return session.finish_reason in ABANDON_REASONS


async def _play(bot: Any, session: Session | LiveSession, events: tuple[DisplayEvent, ...], view: View | LiveView,
                closing_rows: textui.Rows | None = None, closing_text: str | None = None) -> None:
    """Показать события сообщениями с паузами; кнопки — на последнем."""
    bubbles = textui.bubbles(events, view)
    if closing_rows is None and not view.finished:
        closing_rows = textui.keyboard(view)
        closing_text = textui.prompt_text(view)
    if not bubbles:
        bubbles = [textui.Bubble("")]

    last_id = None
    for index, bubble in enumerate(bubbles):
        await _pause(bot, session, bubble.pause + _pace_rng.random() * bubble.spread)
        if _interrupted(session):
            return
        if bubble.cards:
            await _send_cards(bot, session, bubble.cards)
        text = bubble.text
        rows = None
        if index == len(bubbles) - 1:
            rows = closing_rows
            if closing_text:
                text = f"{text}\n\n{closing_text}" if text else closing_text
        if bubble.replace and last_id is not None:
            await _edit(bot, session.chat_id, last_id, text, rows)
        else:
            sent = await _send(bot, session, text, rows)
            last_id = getattr(sent, "message_id", None)
    if not view.finished or closing_rows:
        session.message_id = last_id


async def _log_metrics(session: Session | LiveSession, metrics: tuple[tuple[str, dict[str, Any]], ...]) -> None:
    for event, data in metrics:
        await log_event(
            EXPERIMENT, event, chat_id=session.chat_id, user_id=session.host.user_id,
            session_id=session.session_id, **data,
        )


def _remember_report(session: Session, report: ClientReport) -> None:
    _reports[f"{session.session_id}:{report.client_no}"] = textui.render_report(report)
    while len(_reports) > MAX_CACHED_REPORTS:
        del _reports[next(iter(_reports))]


# --- жизненный цикл партии --------------------------------------------------

def _detach(session: Session | LiveSession, reason: str, now: float) -> dict[str, Any]:
    """Синхронно убрать партию из реестра и зафиксировать итог (до любых await)."""
    _registry.remove(session)
    if isinstance(session, LiveSession):
        return live.abandon_summary(session, reason, now)
    return rules.abandon_summary(session, reason, now)


async def _book_money(session: Session | LiveSession, player: Participant, amount: int) -> None:
    wallets = get_guru_wallets()
    wallets.apply(session.chat_id, player.user_id, player.name, amount)
    await wallets.persist()


async def _log_abandon(session: Session | LiveSession, summary: dict[str, Any]) -> None:
    await log_event(
        EXPERIMENT, "abandon", chat_id=session.chat_id, user_id=session.host.user_id,
        session_id=session.session_id, **summary,
    )


async def _show_abandoned(bot: Any, session: Session | LiveSession, reason: str, *, summary_message: bool = True) -> None:
    await _clear_keyboard(bot, session.chat_id, session.message_id)
    if not summary_message:
        return
    if isinstance(session, LiveSession):
        text, rows = textui.render_live_abandoned(live.build_view(session), reason), None
    else:
        summary, rows = textui.render_summary(rules.build_view(session))
        text = f"{textui.render_abandoned(reason)}\n\n{summary}"
    try:
        await _send(bot, session, text, rows)
    except TelegramAPIError as error:
        logger.warning("[guru] не удалось отправить итог брошенного салона: %s", error)


async def _fail_send(session: Session | LiveSession) -> None:
    logger.exception("[guru] Telegram не принял сообщение салона")
    if _registry.get(session.chat_id) is session:
        await _log_abandon(session, _detach(session, "send_failed", _clock()))


def _claim_chat(chat_id: int, now: float) -> tuple[Any | None, tuple[Any, dict[str, Any]] | None]:
    """Кто занимает чат и какую просроченную партию пришлось снять. Без await: проверка и запись атомарны."""
    current = _registry.get(chat_id)
    if current is not None and rules.is_idle(current, now):
        # Таймаут уже наступил, но цикл ещё не успел его забрать.
        return None, (current, _detach(current, "timeout", now))
    return current, None


async def _reject_busy(bot: Any, chat_id: int, user: Any, thread_id: int | None, via: str, current: Any) -> None:
    await log_event(
        EXPERIMENT, "start_rejected", chat_id=chat_id, user_id=user.id, session_id=current.session_id,
        reason="busy", own=current.host.user_id == user.id, via=via,
    )
    if current.host.user_id == user.id:
        text = "Ты уже принимаешь клиента — жми кнопки выше. Бросить партию: «гуру стоп»."
    else:
        text = f"Салон занят: принимает мадам {current.host.name}. Дождись, пока закроется."
    await bot.send_message(chat_id, text, message_thread_id=thread_id)


async def _close_expired(bot: Any, expired: tuple[Any, dict[str, Any]] | None) -> None:
    if expired is not None:
        old, summary = expired
        await _log_abandon(old, summary)
        await _show_abandoned(bot, old, "timeout", summary_message=False)


async def _start_session(bot: Any, chat_id: int, user: Any, thread_id: int | None, *, via: str,
                         previous: str | None = None) -> None:
    now = _clock()
    current, expired = _claim_chat(chat_id, now)
    if current is not None:
        await _reject_busy(bot, chat_id, user, thread_id, via, current)
        return

    session = rules.new_session(
        session_id=new_session_id(),
        chat_id=chat_id,
        player_id=user.id,
        player_name=_player_name(user),
        rng=_rng,
        now=now,
        wallet=get_guru_wallets().balance(chat_id, user.id),
    )
    session.thread_id = thread_id
    # В реестр — до первого await: второй старт в ту же секунду увидит занятый чат.
    _registry.add(session)
    await _close_expired(bot, expired)

    await log_event(
        EXPERIMENT, "start", chat_id=chat_id, user_id=user.id, session_id=session.session_id,
        via=via, previous=previous, **rules.start_data(session),
    )
    view = rules.build_view(session)
    try:
        await _send(bot, session, textui.render_intro(view))
        await _play(bot, session, rules.arrival_events(session), view)
    except TelegramAPIError:
        await _fail_send(session)


async def _start_live(bot: Any, chat_id: int, user: Any, thread_id: int | None, *, via: str,
                      previous: str | None = None) -> None:
    now = _clock()
    current, expired = _claim_chat(chat_id, now)
    if current is not None:
        await _reject_busy(bot, chat_id, user, thread_id, via, current)
        return

    session = live.new_session(
        session_id=new_session_id(),
        chat_id=chat_id,
        host_id=user.id,
        host_name=_player_name(user),
        now=now,
        wallet=get_guru_wallets().balance(chat_id, user.id),
    )
    session.thread_id = thread_id
    _registry.add(session)
    await _close_expired(bot, expired)

    await log_event(
        EXPERIMENT, "start", chat_id=chat_id, user_id=user.id, session_id=session.session_id,
        via=via, previous=previous, **live.start_data(),
    )
    view = live.build_view(session)
    try:
        sent = await _send(bot, session, textui.render_live_open(view), textui.keyboard(view))
    except TelegramAPIError:
        await _fail_send(session)
        return
    session.message_id = getattr(sent, "message_id", None)


# --- хэндлеры ---------------------------------------------------------------

@router.message(_enabled, _is_start_command)
async def start_salon(message: types.Message) -> None:
    await _start_session(message.bot, message.chat.id, message.from_user, _thread_id(message), via="command")


@router.message(_enabled, _is_live_command)
async def start_live_reception(message: types.Message) -> None:
    if message.chat.type == "private":
        await message.reply("Живому приёму нужен второй человек. Открой его в группе.")
        return
    await _start_live(message.bot, message.chat.id, message.from_user, _thread_id(message), via="command")


@router.message(_enabled, _is_wallet_command)
async def show_wallets(message: types.Message) -> None:
    wallets = get_guru_wallets()
    await message.reply(textui.render_wallets(wallets.top(message.chat.id), wallets.get(message.chat.id, message.from_user.id)))


@router.message(_enabled, _is_stop_command)
async def stop_salon(message: types.Message) -> None:
    session = _registry.get(message.chat.id)
    if session is None:
        await message.reply("Салон и так закрыт. Открыть: «гуру».")
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
    await _clear_keyboard(callback.bot, chat_id, message.message_id)


async def _show_report(callback: types.CallbackQuery, parsed: textui.Callback) -> None:
    text = _reports.pop(f"{parsed.session_id}:{parsed.client_no}", None)
    if text is None:
        await callback.answer("Разбор уже показан или устарел.")
        return
    await callback.answer()
    message = callback.message
    await callback.bot.send_message(
        message.chat.id, text, message_thread_id=_thread_id(message), reply_to_message_id=message.message_id
    )


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
    if parsed.kind == "report":
        await _show_report(callback, parsed)
        return
    if parsed.kind == "livenew":
        await callback.answer()
        await _clear_keyboard(callback.bot, message.chat.id, message.message_id)
        await _start_live(callback.bot, message.chat.id, callback.from_user, _thread_id(message), via="swap",
                          previous=parsed.session_id)
        return

    chat_id = message.chat.id
    session = _registry.get(chat_id)
    if session is None or session.session_id != parsed.session_id or session.step != parsed.step:
        await callback.answer("Эта кнопка уже не работает.")
        return
    if isinstance(session, LiveSession):
        await _live_callback(callback, session, parsed)
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
    action = parsed.action or ""
    try:
        result = rules.act(session, callback.from_user.id, action, _rng, now=_clock())
    except (ValueError, PermissionError):
        await callback.answer("Эта кнопка уже не работает.")
        return
    if session.finished:
        _registry.remove(session)
    if result.finished_client is not None and result.finished_client.report is not None:
        await _book_money(session, session.host, result.finished_client.report.fee)
    await callback.answer()
    await _log_metrics(session, result.metrics)

    bot = callback.bot
    if action.startswith("s:") or action == "reset":
        # Сборка предсказания идёт в одном сообщении.
        text, rows = textui.render_constructor(result.view)
        await _edit(bot, chat_id, message.message_id, text, rows)
        return

    await _clear_keyboard(bot, chat_id, message.message_id)
    try:
        await _deliver(bot, session, result)
    except TelegramAPIError:
        await _fail_send(session)


async def _live_callback(callback: types.CallbackQuery, session: LiveSession, parsed: textui.Callback) -> None:
    user = callback.from_user
    action = parsed.action or ""
    denied = live.refusal(session, user.id, action)
    if denied is not None:
        if not session.is_participant(user.id) and action != "join":
            await log_event(
                EXPERIMENT, "spectator", chat_id=session.chat_id, user_id=user.id,
                session_id=session.session_id, action=action,
            )
        await callback.answer(denied)
        return
    try:
        result = live.act(session, user.id, _player_name(user), action, _rng, now=_clock())
    except (ValueError, PermissionError):
        await callback.answer("Эта кнопка уже не работает.")
        return
    if session.finished:
        _registry.remove(session)
        await _book_money(session, session.host, session.fee)
    await callback.answer()
    await _log_metrics(session, result.metrics)

    bot = callback.bot
    await _clear_keyboard(bot, session.chat_id, callback.message.message_id)
    view = result.view
    try:
        if view.finished:
            await _play(bot, session, result.events, view, textui.live_done_keyboard(view), textui.render_live_result(view))
        else:
            await _play(bot, session, result.events, view)
    except TelegramAPIError:
        await _fail_send(session)


async def _deliver(bot: Any, session: Session, result: Any) -> None:
    view = result.view
    finished = result.finished_client
    if finished is not None and finished.report is not None:
        _remember_report(session, finished.report)
        rows = textui.client_done_keyboard(view, finished.report)
        await _play(bot, session, result.events, view, rows, textui.render_verdict(view, finished.report))
        if view.finished and not _interrupted(session):
            text, summary_rows = textui.render_summary(view)
            await _pause(bot, session, SUMMARY_PAUSE_S)
            await _send(bot, session, text, summary_rows)
        return
    if view.client is not None and view.client.phase in ("who", "what", "when", "announce"):
        text, rows = textui.render_constructor(view)
        await _play(bot, session, result.events, view, rows, text)
        return
    await _play(bot, session, result.events, view)


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
