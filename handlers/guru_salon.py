"""Салон «Гуру» (эксперимент лаборатории ``guru``): тонкий Telegram-слой.

Правила — в ``games.guru.rules``, тексты и кнопки — в ``games.guru.textui``.
Здесь только команды, callback, компактная панель, таймаут и запись метрик ядра в журнал.

Все хэндлеры стоят за ``ExperimentEnabled("guru")``: в чате, где эксперимент
выключен, «гуру» и кнопки уходят дальше по роутерам, как будто салона нет.
Сейчас «гуру» помечен в реестре как ``public``: открыт во всех чатах и в личке.

«гуру» открывает меню с кнопками: кнопку может нажать любой, и действие достаётся
нажавшему. Одна партия на чат: одиночная смена («гуру смена») или живой приём, где
один игрок гадает другому («гуру приём»). Чужие нажатия пишутся как ``spectator``.
Партия без ходов дольше своего лимита бросается фоновым циклом
``guru_salon_timeout_loop`` (запускает composition root через ``TaskSupervisor``).

«гуру карта» — карта дня: не партия, чату не мешает, доступна и во время чужого приёма.

Справки салона — лавка, кассы, салон, полка подарков, салоны чата — это разделы одного меню:
кнопки ``guru:nav:*`` правят то же сообщение, а не шлют новое, и в каждом разделе есть «Меню».
Раздел приходит новым сообщением только по команде или по кнопке под картинкой и в партии,
потому что картинку и ход партии в текст раздела не превратить. Партии и рисунки всегда новые.

Приём занимает одну текстовую панель и одну иллюстрацию. Ход целиком обновляет панель,
а салон, расклад, ладонь и диплом сменяют друг друга в иллюстрации. Предыдущие реплики
доступны по «Диалог», длинные тексты листаются; «Мой салон» открывает салон нажавшего.
Не вышло показать рисунок — текст и кнопки остаются рабочими. Панель размечена HTML.

Деньги игроков копятся в ``features.guru_wallets``: ядро считает сумму, здесь она
зачисляется, как только приём закончен, поэтому брошенная смена заработанного не теряет.

Искусственных пауз между репликами нет. Рисунки создаются через ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from aiogram import F, Router, types
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import BufferedInputFile, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from core.paths import PROJECT_ROOT
from core.settings import ADMIN_ID
from features.guru_wallets import get_guru_wallets
from features.lab.events import log_event, new_session_id
from features.lab.filters import ExperimentEnabled
from games.guru import art, aura_art, cert_art, daily, dialog, live, palm_art, rules, salon, salon_art, seance, textui
from games.guru import content as C
from games.guru.contract import DisplayEvent, LiveView, SeanceView, View
from games.guru.seance import Seance
from games.guru.model import ClientReport, LiveSession, Participant, Session

logger = logging.getLogger(__name__)

EXPERIMENT = "guru"
MENU_COMMANDS = frozenset({"гуру", "гуру меню"})
START_COMMANDS = frozenset({"гуру смена", "гуру играть"})
CARD_COMMANDS = frozenset({"гуру карта", "гуру карта дня"})
STOP_COMMANDS = frozenset({"гуру стоп"})
LIVE_COMMANDS = frozenset({"гуру приём", "гуру прием"})
SEANCE_COMMANDS = frozenset({"гуру сеанс", "гуру людмила"})
WALLET_COMMANDS = frozenset({"гуру касса"})
SHOP_COMMANDS = frozenset({"гуру лавка", "гуру магазин"})
SALON_COMMANDS = frozenset({"гуру салон", "гуру мой салон"})
SALONS_COMMANDS = frozenset({"гуру салоны"})
SHELF_COMMANDS = frozenset({"гуру полка", "гуру подарки"})
TAROT_ART_DIR = PROJECT_ROOT / "assets" / "guru_tarot"
_DRAWINGS = {"palm": palm_art, "aura": aura_art}
SWEEP_INTERVAL_S = 15
MAX_NAME_LENGTH = 24
MAX_CACHED_REPORTS = 300
ABANDON_REASONS = frozenset({"timeout", "quit", "restart", "send_failed"})
CAPTION_LIMIT = 1000  # у Telegram 1024, оставляем запас
BRAND = "@expertyebaniebot · напиши «гуру»"  # подпись на справке и дипломе: по ней переславшие находят игру
MOSCOW = timezone(timedelta(hours=3))  # день карты меняется в полночь по Москве; tzdata для этого не нужна

router = Router(name="guru_salon")
_enabled = ExperimentEnabled(EXPERIMENT)

_registry = rules.SessionRegistry()
_rng = random.Random()
_reports: dict[str, str] = {}  # подробные разборы до нажатия кнопки; живут в памяти
_card_file_ids: dict[str, str] = {}  # рисунок уже загружен в Telegram: повторно не отправляем файл
_closed_dialogs: dict[str, Session | LiveSession | Seance] = {}  # журнал завершённых приёмов


def _clock() -> float:
    return time.monotonic()


def _today() -> date:
    return datetime.now(MOSCOW).date()


# --- разбор команд ----------------------------------------------------------

def _normalize(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _is_menu_command(message: types.Message) -> bool:
    return _normalize(message.text) in MENU_COMMANDS


def _is_start_command(message: types.Message) -> bool:
    return _normalize(message.text) in START_COMMANDS


def _is_card_command(message: types.Message) -> bool:
    return _normalize(message.text) in CARD_COMMANDS


def _is_stop_command(message: types.Message) -> bool:
    return _normalize(message.text) in STOP_COMMANDS


def _is_live_command(message: types.Message) -> bool:
    return _normalize(message.text) in LIVE_COMMANDS


def _is_seance_command(message: types.Message) -> bool:
    return _normalize(message.text) in SEANCE_COMMANDS


def _is_wallet_command(message: types.Message) -> bool:
    return _normalize(message.text) in WALLET_COMMANDS


def _is_shop_command(message: types.Message) -> bool:
    return _normalize(message.text) in SHOP_COMMANDS


def _is_salon_command(message: types.Message) -> bool:
    return _normalize(message.text) in SALON_COMMANDS


def _is_salons_command(message: types.Message) -> bool:
    return _normalize(message.text) in SALONS_COMMANDS


def _is_shelf_command(message: types.Message) -> bool:
    return _normalize(message.text) in SHELF_COMMANDS


def _salon_owner(message: types.Message) -> tuple[Any, bool]:
    """Чей салон смотрят: свой или, в ответ на сообщение другой мадам, её. Второе значение — в гостях ли смотрящий."""
    replied = getattr(getattr(message, "reply_to_message", None), "from_user", None)
    guest = replied is not None and not getattr(replied, "is_bot", False) and replied.id != message.from_user.id
    return (replied if guest else message.from_user), guest


def _player_name(user: Any) -> str:
    name = (getattr(user, "first_name", None) or getattr(user, "username", None) or "Аноним").strip()
    return name[:MAX_NAME_LENGTH] or "Аноним"


def _thread_id(message: Any) -> int | None:
    return getattr(message, "message_thread_id", None) if getattr(message, "is_topic_message", False) else None


def _is_group(chat_id: int) -> bool:
    """В Telegram у личных чатов номер положительный, у групп и каналов — отрицательный."""
    return chat_id < 0


def _markup(rows: textui.Rows | None) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows]
    )


# --- Telegram I/O -----------------------------------------------------------

def _mode(html: bool) -> str | None:
    return "HTML" if html else None


async def _send(bot: Any, session: Session | LiveSession, text: str, rows: textui.Rows | None = None, *,
                reply_to: int | None = None, html: bool = False) -> Any:
    return await bot.send_message(
        session.chat_id,
        text,
        message_thread_id=session.thread_id,
        reply_markup=_markup(rows),
        reply_to_message_id=reply_to,
        parse_mode=_mode(html),
    )


async def _edit(bot: Any, chat_id: int, message_id: int | None, text: str, rows: textui.Rows | None = None, *,
                html: bool = False) -> None:
    if message_id is None:
        return
    try:
        await bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=_markup(rows),
                                    parse_mode=_mode(html))
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


def _interrupted(session: Session | LiveSession) -> bool:
    return session.finish_reason in ABANDON_REASONS


async def _play(bot: Any, session: Session | LiveSession | Seance, events: tuple[DisplayEvent, ...], view: View | LiveView | SeanceView,
                closing_rows: textui.Rows | None = None, closing_text: str | None = None, *, intro: str = "") -> None:
    """Один ход — одно обновление диалога. Иллюстрация тоже остаётся на своём месте."""
    if closing_rows is None and not view.finished:
        closing_rows = textui.keyboard(view)
        closing_text = textui.prompt_text(view)
    parts = [textui.escape(intro)] if intro else []
    for bubble in textui.bubbles(events, view):
        if bubble.text:
            parts.append(bubble.text)
        if bubble.scene:
            client, items, gifts = bubble.scene
            key = f"salon:{client or '-'}:{'+'.join(sorted(items))}:{'+'.join(sorted(gifts))}"
            await _illustration(bot, session, key, salon_art.render, (items, client, gifts), "salon.jpg")
        if bubble.cards:
            await _illustration(bot, session, "spread:" + "+".join(bubble.cards), art.render_spread, (bubble.cards,), "spread.jpg")
        if bubble.drawing:
            kind, marks, *extra = bubble.drawing
            hand = extra[0] if extra else None
            key = f"{kind}:" + "+".join(sorted(marks)) + (f":{hand}" if hand else "")
            painter = palm_art.render_study if kind == "study" else _DRAWINGS[kind].render
            args = (marks, hand) if hand else (marks,)
            await _illustration(bot, session, key, painter, args, "palm.jpg" if kind == "study" else f"{kind}.jpg")
    if _interrupted(session):
        return
    session.dialog.history.extend(parts)
    if closing_text:
        parts.append(textui.escape(closing_text))
    await _panel(bot, session, parts, closing_rows or [])


def _cache_dialog(session: Session | LiveSession | Seance) -> None:
    _closed_dialogs[session.session_id] = session
    while len(_closed_dialogs) > MAX_CACHED_REPORTS:
        del _closed_dialogs[next(iter(_closed_dialogs))]


async def _panel(bot: Any, session: Session | LiveSession | Seance, parts: list[str], rows: textui.Rows) -> None:
    session.dialog.pages = dialog.pages(parts)
    session.dialog.rows = rows or []
    if session.finished:
        _cache_dialog(session)
    await _dialog_page(bot, session, "screen", 0)


async def _dialog_page(bot: Any, session: Session | LiveSession | Seance, mode: str, page: int) -> None:
    if mode == "heard" and isinstance(session, Seance):
        pages = dialog.pages([textui.escape(line) for line in session.heard])
    else:
        pages = dialog.pages(session.dialog.history) if mode == "log" else session.dialog.pages
    page = min(max(page, 0), len(pages) - 1)
    heading = "📜 Что сказала Людмила" if mode == "heard" else "📜 Диалог" if mode == "log" else f"🕯 Мадам {session.host.name}"
    if len(pages) > 1:
        heading += f" · {page + 1}/{len(pages)}"
    text = f"<b>{textui.escape(heading)}</b>\n\n{pages[page]}"
    rows = textui.dialog_keyboard(session.session_id, session.step, [] if mode != "screen" else session.dialog.rows,
                                  mode=mode, page=page, total=len(pages), history=bool(session.dialog.history))
    if session.message_id is not None:
        try:
            await bot.edit_message_text(text=text, chat_id=session.chat_id, message_id=session.message_id,
                                        reply_markup=_markup(rows), parse_mode="HTML")
            return
        except TelegramBadRequest as error:
            detail = str(error).lower()
            if "not modified" in detail:
                return
            if "message to edit not found" not in detail and "message can't be edited" not in detail:
                raise
    sent = await _send(bot, session, text, rows, html=True)
    session.message_id = sent.message_id


async def _illustration(bot: Any, session: Session | LiveSession | Seance, key: str, painter: Any, args: tuple,
                        filename: str) -> None:
    """Одно фото на приём: меняются салон, расклад, ладонь и итоговая справка."""
    if key == session.dialog.media_key:
        return
    try:
        photo = _card_file_ids.get(key) or BufferedInputFile(await asyncio.to_thread(painter, *args), filename)
    except Exception:
        logger.exception("[guru] не удалось нарисовать иллюстрацию")
        return
    if _interrupted(session):
        return
    try:
        sent = None
        if session.dialog.media_id is not None:
            try:
                sent = await bot.edit_message_media(chat_id=session.chat_id, message_id=session.dialog.media_id,
                                                    media=InputMediaPhoto(media=photo))
            except TelegramBadRequest as error:
                detail = str(error).lower()
                if "not modified" in detail:
                    session.dialog.media_key = key
                    return
                if "message to edit not found" not in detail and "message can't be edited" not in detail:
                    raise
        if sent is None:
            sent = await bot.send_photo(chat_id=session.chat_id, photo=photo, message_thread_id=session.thread_id,
                                        disable_notification=True)
        session.dialog.media_id, session.dialog.media_key = sent.message_id, key
        if getattr(sent, "photo", None) and not key.startswith(("certificate:", "diploma:")):
            _card_file_ids[key] = sent.photo[-1].file_id
    except (TelegramAPIError, OSError):
        logger.warning("[guru] не удалось обновить иллюстрацию", exc_info=True)


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
    if isinstance(session, Seance):
        return seance.abandon_summary(session, reason, now)
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
    elif isinstance(session, Seance):
        text, rows = textui.render_seance_abandoned(reason), textui.seance_done_keyboard()
    else:
        summary, rows = textui.render_summary(rules.build_view(session), _is_group(session.chat_id))
        text = f"{textui.render_abandoned(reason)}\n\n{summary}"
    try:
        await _panel(bot, session, [textui.escape(text)], rows or [])
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

    wallet = get_guru_wallets().get(chat_id, user.id)
    session = rules.new_session(
        session_id=new_session_id(),
        chat_id=chat_id,
        player_id=user.id,
        player_name=_player_name(user),
        rng=_rng,
        now=now,
        wallet=wallet.balance,
        items=tuple(wallet.items),
        novice=wallet.shifts < rules.NOVICE_SHIFTS,
        gifts=tuple(wallet.gifts),
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
        await _play(bot, session, rules.arrival_events(session), view, intro=textui.render_intro(view))
    except TelegramAPIError:
        await _fail_send(session)


async def _start_live(bot: Any, chat_id: int, user: Any, thread_id: int | None, *, via: str,
                      previous: str | None = None) -> None:
    now = _clock()
    current, expired = _claim_chat(chat_id, now)
    if current is not None:
        await _reject_busy(bot, chat_id, user, thread_id, via, current)
        return

    wallet = get_guru_wallets().get(chat_id, user.id)
    session = live.new_session(
        session_id=new_session_id(),
        chat_id=chat_id,
        host_id=user.id,
        host_name=_player_name(user),
        now=now,
        wallet=wallet.balance,
        items=tuple(wallet.items),
        gifts=tuple(wallet.gifts),
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
        await _panel(bot, session, [textui.escape(textui.render_live_open(view))], textui.keyboard(view))
    except TelegramAPIError:
        await _fail_send(session)
        return


async def _start_seance(bot: Any, chat_id: int, user: Any, thread_id: int | None, *, via: str) -> None:
    """Сеанс с одной клиенткой: мадам сама толкует знаки и решает, что сказать."""
    now = _clock()
    current, expired = _claim_chat(chat_id, now)
    if current is not None:
        await _reject_busy(bot, chat_id, user, thread_id, via, current)
        return

    wallet = get_guru_wallets().get(chat_id, user.id)
    session = seance.new_session(
        session_id=new_session_id(), chat_id=chat_id, host_id=user.id, host_name=_player_name(user), now=now,
        wallet=wallet.balance, items=tuple(wallet.items), gifts=tuple(wallet.gifts),
    )
    session.thread_id = thread_id
    _registry.add(session)
    await _close_expired(bot, expired)

    await log_event(EXPERIMENT, "start", chat_id=chat_id, user_id=user.id, session_id=session.session_id, via=via,
                    **seance.start_data(session))
    view = seance.build_view(session)
    try:
        text, rows = textui.render_seance(view)
        await _play(bot, session, seance.arrival_events(session), view, rows, text, intro=textui.render_seance_intro(view))
    except TelegramAPIError:
        await _fail_send(session)


# --- меню, карта дня и справки ----------------------------------------------

async def _post(bot: Any, chat_id: int, thread_id: int | None, text: str, rows: textui.Rows | None = None, *,
                reply_to: int | None = None) -> Any:
    """Сообщение вне партии: меню, касса, лавка. ``reply_to`` — когда отвечаем на команду."""
    return await bot.send_message(chat_id, text, message_thread_id=thread_id, reply_markup=_markup(rows),
                                  reply_to_message_id=reply_to)


def _section(chat_id: int, user: Any, section: str) -> tuple[str, textui.Rows]:
    """Раздел меню салона глазами ``user``: текст и кнопки. Одинаков для нового сообщения и для правки прежнего."""
    wallets = get_guru_wallets()
    wallet = wallets.get(chat_id, user.id)
    if section == "shop":
        return textui.render_shop(_player_name(user), wallet.balance, wallet.items), textui.shop_keyboard(wallet.items, user.id)
    if section == "wallet":
        return textui.render_wallets(wallets.top(chat_id), wallet), textui.section_keyboard(section)
    if section == "salons":
        return textui.render_salons(wallets.top(chat_id), wallet), textui.section_keyboard(section)
    if section == "shelf":
        return textui.render_shelf(wallet.name or _player_name(user), wallet), textui.section_keyboard(section)
    if section == "salon":
        return textui.render_salon_card(wallet.name or _player_name(user), wallet), textui.section_keyboard(section)
    group = _is_group(chat_id)
    return textui.render_menu(_player_name(user), wallet, group), textui.menu_keyboard(group)


async def _open_section(bot: Any, chat_id: int, user: Any, thread_id: int | None, section: str, *,
                        reply_to: int | None = None) -> None:
    """Раздел меню новым сообщением: по команде или по кнопке под картинкой. Дальше разделы листаются в нём же."""
    text, rows = _section(chat_id, user, section)
    await _post(bot, chat_id, thread_id, text, rows, reply_to=reply_to)


async def _show_menu(bot: Any, chat_id: int, user: Any, thread_id: int | None, *, reply_to: int | None = None) -> None:
    await log_event(EXPERIMENT, "menu", chat_id=chat_id, user_id=user.id, action="open")
    await _open_section(bot, chat_id, user, thread_id, "menu", reply_to=reply_to)


async def _show_salon(bot: Any, chat_id: int, owner: Any, thread_id: int | None, *, visitor: bool = False) -> None:
    """Салон одного игрока картинкой: что куплено, что это даёт и что стоит на полке."""
    wallet = get_guru_wallets().get(chat_id, owner.id)
    text = textui.render_salon_card(wallet.name or _player_name(owner), wallet, visitor)
    rows = textui.salon_keyboard()
    if await _send_salon(bot, chat_id, thread_id, None, tuple(wallet.items), tuple(wallet.gifts), text, rows) is None:
        await _post(bot, chat_id, thread_id, text, rows)


async def _send_picture(bot: Any, chat_id: int, thread_id: int | None, photo: Any, caption: str,
                        rows: textui.Rows | None = None, *, cache_key: str | None = None, html: bool = False) -> Any | None:
    """Картинка с подписью и кнопками. ``None`` — не вышло: вызывающий отправит то же текстом."""
    if len(caption) > CAPTION_LIMIT:
        return None
    try:
        sent = await bot.send_photo(chat_id=chat_id, photo=photo, caption=caption, message_thread_id=thread_id,
                                    reply_markup=_markup(rows), parse_mode=_mode(html))
    except (TelegramAPIError, OSError) as error:
        logger.warning("[guru] не удалось отправить картинку: %s", error)
        return None
    if cache_key and getattr(sent, "photo", None):
        _card_file_ids[cache_key] = sent.photo[-1].file_id
    return sent


async def _send_salon(bot: Any, chat_id: int, thread_id: int | None, client: str | None, items: tuple[str, ...],
                      gifts: tuple[str, ...], caption: str, rows: textui.Rows | None = None, *, html: bool = False) -> Any | None:
    """Рисунок салона с подписью. Каждое сочетание вещей, клиента и подарков рисуется один раз."""
    key = f"salon:{client or '-'}:{'+'.join(sorted(items))}:{'+'.join(sorted(gifts))}"
    photo = _card_file_ids.get(key)
    if photo is None:
        try:
            photo = BufferedInputFile(await asyncio.to_thread(salon_art.render, items, client, gifts), "salon.jpg")
        except Exception:
            logger.exception("[guru] не удалось нарисовать салон")
            return None
    return await _send_picture(bot, chat_id, thread_id, photo, caption, rows, cache_key=key, html=html)


async def _daily_card(bot: Any, chat_id: int, user: Any, thread_id: int | None, *, via: str) -> None:
    """Карта дня: одна на человека в сутки. Не партия: чат не занимает и чужому приёму не мешает."""
    today = _today()
    name = _player_name(user)
    card = daily.draw(user.id, today.isoformat())
    wallets = get_guru_wallets()
    # Отметка и чаевые — до первого await: двойное нажатие не заплатит дважды.
    streak = wallets.claim_card(chat_id, user.id, name, today.isoformat(), (today - timedelta(days=1)).isoformat())
    if streak is None:
        await log_event(EXPERIMENT, "daily_card", chat_id=chat_id, user_id=user.id, via=via, repeat=True)
        await _post(bot, chat_id, thread_id, textui.render_daily_repeat(name, card))
        return
    tip = daily.tip(streak)
    balance = wallets.apply(chat_id, user.id, name, tip)
    await wallets.persist()
    await log_event(
        EXPERIMENT, "daily_card", chat_id=chat_id, user_id=user.id, via=via, repeat=False,
        card=card.card.art, streak=streak, tip=tip, wallet=balance, **card.picks,
    )
    caption = textui.render_daily(name, card, tip, streak, daily.tip(streak + 1), balance)
    rows = textui.daily_keyboard()
    art = card.card.art
    photo = _card_file_ids.get(art) or FSInputFile(TAROT_ART_DIR / f"{art}.jpg")
    if await _send_picture(bot, chat_id, thread_id, photo, caption, rows, cache_key=art) is None:
        await _post(bot, chat_id, thread_id, caption, rows)


# --- хэндлеры ---------------------------------------------------------------

@router.message(_enabled, _is_menu_command)
async def show_menu(message: types.Message) -> None:
    await _show_menu(message.bot, message.chat.id, message.from_user, _thread_id(message), reply_to=message.message_id)


@router.message(_enabled, _is_start_command)
async def start_salon(message: types.Message) -> None:
    await _start_session(message.bot, message.chat.id, message.from_user, _thread_id(message), via="command")


@router.message(_enabled, _is_live_command)
async def start_live_reception(message: types.Message) -> None:
    if message.chat.type == "private":
        await message.reply(textui.LIVE_NEEDS_GROUP)
        return
    await _start_live(message.bot, message.chat.id, message.from_user, _thread_id(message), via="command")


@router.message(_enabled, _is_seance_command)
async def start_seance(message: types.Message) -> None:
    await _start_seance(message.bot, message.chat.id, message.from_user, _thread_id(message), via="command")


@router.message(_enabled, _is_card_command)
async def show_daily_card(message: types.Message) -> None:
    await _daily_card(message.bot, message.chat.id, message.from_user, _thread_id(message), via="command")


@router.message(_enabled, _is_wallet_command)
async def show_wallets(message: types.Message) -> None:
    await _open_section(message.bot, message.chat.id, message.from_user, _thread_id(message), "wallet", reply_to=message.message_id)


@router.message(_enabled, _is_shop_command)
async def show_shop(message: types.Message) -> None:
    await _open_section(message.bot, message.chat.id, message.from_user, _thread_id(message), "shop", reply_to=message.message_id)


@router.message(_enabled, _is_salon_command)
async def show_salon(message: types.Message) -> None:
    """Свой салон; в ответ на сообщение другой мадам — её салон."""
    owner, guest = _salon_owner(message)
    await _show_salon(message.bot, message.chat.id, owner, _thread_id(message), visitor=guest)


@router.message(_enabled, _is_shelf_command)
async def show_shelf(message: types.Message) -> None:
    """Своя полка подарков; в ответ на сообщение другой мадам — её полка."""
    owner, _ = _salon_owner(message)
    await _open_section(message.bot, message.chat.id, owner, _thread_id(message), "shelf", reply_to=message.message_id)


@router.message(_enabled, _is_salons_command)
async def show_salons(message: types.Message) -> None:
    await _open_section(message.bot, message.chat.id, message.from_user, _thread_id(message), "salons", reply_to=message.message_id)


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


async def _menu(callback: types.CallbackQuery, parsed: textui.Callback) -> None:
    """Кнопка меню: действие достаётся тому, кто нажал, и приходит новым сообщением. Сообщение с кнопкой не меняется."""
    message = callback.message
    bot, chat_id, user, thread_id = callback.bot, message.chat.id, callback.from_user, _thread_id(message)
    action = parsed.action
    if action == "live" and not _is_group(chat_id):
        await callback.answer(textui.LIVE_NEEDS_GROUP)
        return
    if action == "card":
        today = _today().isoformat()
        if get_guru_wallets().get(chat_id, user.id).card_day == today:
            # Повтор по кнопке — всплывающей строкой: чужая карта в чате не должна обрастать ответами.
            await callback.answer(textui.render_daily_toast(daily.draw(user.id, today)))
            await log_event(EXPERIMENT, "daily_card", chat_id=chat_id, user_id=user.id, via="button", repeat=True)
            return
    await callback.answer()
    await log_event(EXPERIMENT, "menu", chat_id=chat_id, user_id=user.id, action=action)
    if action == "shift":
        await _start_session(bot, chat_id, user, thread_id, via="menu")
    elif action == "live":
        await _start_live(bot, chat_id, user, thread_id, via="menu")
    elif action == "seance":
        await _start_seance(bot, chat_id, user, thread_id, via="menu")
    elif action == "card":
        await _daily_card(bot, chat_id, user, thread_id, via="button")
    elif action == "salon":
        await _show_salon(bot, chat_id, user, thread_id)
    else:
        # Кнопка стоит под картинкой или в чужой партии: то сообщение править нельзя, раздел открывается своим.
        await _open_section(bot, chat_id, user, thread_id, action)


async def _nav(callback: types.CallbackQuery, parsed: textui.Callback) -> None:
    """Раздел меню в том же сообщении. Показывается глазами нажавшего: в группе меню общее, но касса у каждого своя."""
    message = callback.message
    chat_id, user = message.chat.id, callback.from_user
    await callback.answer()
    await log_event(EXPERIMENT, "menu", chat_id=chat_id, user_id=user.id, action="back" if parsed.action == "menu" else parsed.action,
                    inplace=True)
    text, rows = _section(chat_id, user, parsed.action)
    await _edit(callback.bot, chat_id, message.message_id, text, rows)


async def _buy(callback: types.CallbackQuery, parsed: textui.Callback) -> None:
    user = callback.from_user
    if user.id != parsed.owner:
        await callback.answer("Это чужая лавка. Открой свою: «гуру лавка».")
        return
    chat_id = callback.message.chat.id
    wallets = get_guru_wallets()
    wallet = wallets.get(chat_id, user.id)
    denied = salon.refusal(parsed.item, wallet.balance, wallet.items)
    if denied is not None:
        await callback.answer(denied)
        return
    item = salon.ITEMS[parsed.item]
    name = _player_name(user)
    if not wallets.buy(chat_id, user.id, name, item.key, item.price):
        await callback.answer("Эта кнопка уже не работает.")
        return
    await wallets.persist()
    wallet = wallets.get(chat_id, user.id)
    await log_event(EXPERIMENT, "purchase", chat_id=chat_id, user_id=user.id, item=item.key, price=item.price, wallet=wallet.balance)
    await callback.answer(f"Куплено: {item.name}")
    await _edit(callback.bot, chat_id, callback.message.message_id, textui.render_shop(name, wallet.balance, wallet.items),
                textui.shop_keyboard(wallet.items, user.id))
    # Покупку видят все: салон с новой вещью на своём месте.
    text, thread_id = textui.render_purchase(item, name, wallet.balance), _thread_id(callback.message)
    if await _send_salon(callback.bot, chat_id, thread_id, None, tuple(wallet.items), tuple(wallet.gifts), text) is not None:
        return
    try:
        await callback.bot.send_message(chat_id, text, message_thread_id=thread_id)
    except TelegramAPIError as error:
        logger.warning("[guru] не удалось объявить покупку: %s", error)


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
    if parsed.kind == "menu":
        await _menu(callback, parsed)
        return
    if parsed.kind == "nav":
        await _nav(callback, parsed)
        return
    if parsed.kind == "again":
        await _again(callback, parsed)
        return
    if parsed.kind == "report":
        await _show_report(callback, parsed)
        return
    if parsed.kind == "buy":
        await _buy(callback, parsed)
        return
    if parsed.kind == "livenew":
        await callback.answer()
        await _clear_keyboard(callback.bot, message.chat.id, message.message_id)
        await _start_live(callback.bot, message.chat.id, callback.from_user, _thread_id(message), via="swap",
                          previous=parsed.session_id)
        return

    chat_id = message.chat.id
    session = _registry.get(chat_id)
    if parsed.kind == "dialog":
        # Журнал доступен и после завершения; игровые действия по старым кнопкам не принимаем.
        target = session if session is not None and session.session_id == parsed.session_id else _closed_dialogs.get(parsed.session_id)
        if target is None or target.chat_id != chat_id or target.step != parsed.step:
            await callback.answer("Этот диалог уже устарел.")
            return
        if not target.is_participant(callback.from_user.id):
            await callback.answer(C.SPECTATOR_LINE.format(name=target.host.name))
            return
        if message.message_id != target.message_id:
            await callback.answer("Открой диалог под текущим сообщением.")
            return
        if not target.finished:
            target.last_activity = _clock()
        await callback.answer()
        await _dialog_page(callback.bot, target, parsed.action or "screen", parsed.page)
        return
    if session is None or session.session_id != parsed.session_id or session.step != parsed.step:
        await callback.answer("Эта кнопка уже не работает.")
        return
    if isinstance(session, LiveSession):
        try:
            await _live_callback(callback, session, parsed)
        except TelegramAPIError:
            await _fail_send(session)
        return
    if isinstance(session, Seance):
        await _seance_callback(callback, session, parsed)
        return
    if not session.is_participant(callback.from_user.id):
        await log_event(
            EXPERIMENT, "spectator", chat_id=chat_id, user_id=callback.from_user.id,
            session_id=session.session_id, action=parsed.action,
        )
        await callback.answer(C.SPECTATOR_LINE.format(name=session.host.name))
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
        if len(session.reports) == 1:
            # Смена засчитана с первого принятого клиента: брошенная на пороге не тратит везение новичка.
            get_guru_wallets().count_shift(chat_id, session.host.user_id, session.host.name)
        if result.finished_client.report.gift:
            get_guru_wallets().add_gift(chat_id, session.host.user_id, session.host.name, result.finished_client.report.gift)
        await _book_money(session, session.host, result.finished_client.report.fee)
    await callback.answer()
    await _log_metrics(session, result.metrics)

    bot = callback.bot
    if action.startswith("s:") or action == "reset":
        # Сборка предсказания идёт в одном сообщении.
        text, rows = textui.render_constructor(result.view)
        try:
            await _panel(bot, session, [textui.escape(text)], rows)
        except TelegramAPIError:
            await _fail_send(session)
        return

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
    await callback.answer(result.notice)
    await _log_metrics(session, result.metrics)

    bot = callback.bot
    view = result.view
    if action.startswith("q:"):
        # Анкета заполняется в одном сообщении, как сборка предсказания в смене.
        text, rows = textui.render_intake(view)
        await _panel(bot, session, [textui.escape(text)], rows)
        if view.phase == "intake":
            return
    elif action.startswith("f:"):
        text, rows = textui.render_live_constructor(view)
        await _panel(bot, session, [textui.escape(text)], rows)
        return
    try:
        if view.finished:
            await _finish_live(bot, session, result.events, view)
        elif action == "go":
            text, rows = textui.render_live_constructor(view)
            await _play(bot, session, result.events, view, rows, text)
        else:
            await _play(bot, session, result.events, view)
    except TelegramAPIError:
        await _fail_send(session)


async def _seance_callback(callback: types.CallbackQuery, session: Seance, parsed: textui.Callback) -> None:
    user, bot = callback.from_user, callback.bot
    action = parsed.action or ""
    if not session.is_participant(user.id):
        await log_event(EXPERIMENT, "spectator", chat_id=session.chat_id, user_id=user.id, session_id=session.session_id, action=action)
        await callback.answer(C.SPECTATOR_LINE.format(name=session.host.name))
        return
    try:
        result = seance.act(session, user.id, action, now=_clock())
    except (ValueError, PermissionError):
        await callback.answer("Эта кнопка уже не работает.")
        return
    seen = 0
    if session.finished:
        _registry.remove(session)
        seen = get_guru_wallets().see_ending(session.chat_id, user.id, session.host.name, session.ending or "")
        await _book_money(session, session.host, session.fee)
    await callback.answer()
    await _log_metrics(session, result.metrics)

    view = result.view
    try:
        if action == "heard":
            # Перечитать без нового сообщения; назад вернётся тот же игровой экран.
            await _dialog_page(bot, session, "heard", 0)
            return
        text, rows = textui.render_seance(view)
        if not result.fresh:
            await _panel(bot, session, [textui.escape(text)], rows)
            return
        if view.finished:
            await _play(bot, session, result.events, view, textui.seance_done_keyboard(),
                        textui.render_seance_result(view, seen, len(seance.ENDINGS)))
        else:
            await _play(bot, session, result.events, view, rows, text)
    except TelegramAPIError:
        await _fail_send(session)


async def _finish_live(bot: Any, session: LiveSession, events: tuple[DisplayEvent, ...], view: LiveView) -> None:
    """Развязка живого приёма и справка о предсказании: её удобно переслать."""
    text, rows = textui.render_live_result(view), textui.live_done_keyboard(view)
    await _illustration(bot, session, f"certificate:{session.session_id}", cert_art.render_certificate,
                        (live.certificate(session), BRAND), "guru.jpg")
    await _play(bot, session, events, view, rows, text)


async def _deliver(bot: Any, session: Session, result: Any) -> None:
    view = result.view
    finished = result.finished_client
    if finished is not None and finished.report is not None:
        _remember_report(session, finished.report)
        rows = textui.client_done_keyboard(view, finished.report)
        text = textui.render_verdict(view, finished.report)
        if view.finished and not _interrupted(session):
            summary, rows = textui.render_summary(view, _is_group(session.chat_id))
            text += f"\n\n{summary}"
            await _illustration(bot, session, f"diploma:{session.session_id}", cert_art.render_diploma,
                                (rules.diploma(session), BRAND), "guru.jpg")
        await _play(bot, session, result.events, view, rows, text)
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
