"""Telegram UX for the chat Chronicle."""

from __future__ import annotations

from html import escape
import re
from zoneinfo import ZoneInfo

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings import APP_TIMEZONE_NAME
from features.chronicle import config
from features.chronicle.runtime import count_events, list_events, request_backfill


router = Router(name="chronicle")
_USER_RE = re.compile(r"^летопись\s+@([A-Za-z0-9_]{3,64})$", re.IGNORECASE)
_HALL_OF_FAME_COMMANDS = {"зал славы", "летопись лучшее"}
CALLBACK_PREFIX = "chr:"
MODE_RECENT = "r"
MODE_HALL = "h"


def _command_kind(message: types.Message) -> tuple[str, str | None] | None:
    text = (message.text or "").strip()
    if text.casefold() == "летопись":
        return "chronicle", None
    if " ".join(text.casefold().split()) in _HALL_OF_FAME_COMMANDS:
        return "hall", None
    match = _USER_RE.fullmatch(text)
    if match:
        return "chronicle", match.group(1)
    return None


def _is_group(message: types.Message) -> bool:
    return getattr(message.chat.type, "value", message.chat.type) in {"group", "supergroup"}


def _message_url(message: types.Message, message_id: int | None) -> str | None:
    if not message_id:
        return None
    if getattr(message.chat, "username", None):
        return f"https://t.me/{message.chat.username}/{int(message_id)}"
    raw = str(abs(int(message.chat.id)))
    if raw.startswith("100") and len(raw) > 3:
        return f"https://t.me/c/{raw[3:]}/{int(message_id)}"
    return None


def _event_date(value) -> str:
    local = value.astimezone(ZoneInfo(APP_TIMEZONE_NAME))
    months = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    return f"{local.day} {months[local.month - 1]}"


def _backfill_progress(state: dict | None) -> str | None:
    if not state:
        return None
    status = str(state.get("status") or "")
    if status == "completed":
        return None
    if status in {"pending", "running"}:
        return "⏳ Ретроспектива: готовлю первичный индекс доступной истории чата."
    if status == "scanning":
        scanned = max(0, int(state.get("scanned_messages") or 0))
        total = max(0, int(state.get("total_messages") or 0))
        if total:
            percent = min(100, round(scanned * 100 / total))
            return f"⏳ Ретроспектива: локально просмотрено {scanned} из {total} сообщений ({percent}%)."
        return f"⏳ Ретроспектива: локально просмотрено {scanned} сообщений."
    if status == "ranking":
        return "⏳ Ретроспектива: локальный проход завершён, ранжирую самые сильные эпизоды."
    if status == "classifying":
        done = max(0, int(state.get("ai_requests") or 0))
        queued = max(0, int(state.get("queued_candidates") or 0))
        if queued:
            return f"⏳ Ретроспектива: вся доступная история просмотрена; проверяю лучшие эпизоды через AI ({min(done, queued)}/{queued})."
        return "⏳ Ретроспектива: вся доступная история просмотрена; проверяю лучшие эпизоды через AI."
    return "⏳ Ретроспективная индексация истории ещё идёт."


def _format(
    message: types.Message,
    events,
    target_label: str | None,
    progress: str | None = None,
    *,
    mode: str = MODE_RECENT,
    page: int = 0,
    pages: int = 1,
) -> str:
    if mode == MODE_HALL:
        header = "🏆 <b>Зал славы Летописи</b>" if target_label is None else f"🏆 <b>Зал славы {escape(target_label)}</b>"
    else:
        header = "📜 <b>Летопись чата</b>" if target_label is None else f"📜 <b>Летопись {escape(target_label)}</b>"
    if pages > 1:
        header += f" · стр. {page + 1}/{pages}"
    lines = [header]
    for event in events:
        lines.append("")
        lines.append(f"<b>{_event_date(event.event_started_at)} — {escape(event.title)}</b>")
        lines.append(escape(event.summary))
        url = _message_url(message, event.anchor_message_id)
        if url:
            lines.append(f'<a href="{escape(url, quote=True)}">↗ к месту преступления</a>')
    if progress:
        lines.extend(["", escape(progress)])
    return "\n".join(lines)


def _encode_filter(user_id: int | None, username: str | None) -> str:
    if user_id is not None:
        return f"i{int(user_id)}"
    if username:
        return f"u{username}"
    return "a"


def _decode_filter(raw: str) -> tuple[int | None, str | None]:
    if raw.startswith("i"):
        try:
            return int(raw[1:]), None
        except ValueError:
            return None, None
    if raw.startswith("u") and len(raw) > 1:
        return None, raw[1:]
    return None, None


def chronicle_keyboard(mode: str, page: int, pages: int, filter_code: str = "a"):
    builder = InlineKeyboardBuilder()
    navigation = 0
    if page > 0:
        builder.button(text="◀", callback_data=f"{CALLBACK_PREFIX}{mode}:{page - 1}:{filter_code}")
        navigation += 1
    if page + 1 < pages:
        builder.button(text="▶", callback_data=f"{CALLBACK_PREFIX}{mode}:{page + 1}:{filter_code}")
        navigation += 1
    other = MODE_RECENT if mode == MODE_HALL else MODE_HALL
    builder.button(
        text="🕰 По времени" if other == MODE_RECENT else "🏆 Зал славы",
        callback_data=f"{CALLBACK_PREFIX}{other}:0:{filter_code}",
    )
    builder.adjust(*([navigation] if navigation else []), 1)
    return builder.as_markup()


async def _page(chat_id: int, mode: str, page: int, user_id: int | None, username: str | None):
    order = "importance" if mode == MODE_HALL else "recent"
    page_size = config.DISPLAY_EVENT_LIMIT
    total = await count_events(chat_id, user_id=user_id, username=username)
    pages = max(1, -(-total // page_size))
    page = max(0, min(page, pages - 1))
    events = await list_events(
        chat_id,
        user_id=user_id,
        username=username,
        limit=page_size,
        offset=page * page_size,
        order=order,
    )
    return events, page, pages


async def send_chronicle_page(
    target: types.Message,
    *,
    mode: str = MODE_RECENT,
    page: int = 0,
    user_id: int | None = None,
    username: str | None = None,
    target_label: str | None = None,
    edit: bool = False,
) -> bool:
    """Render one Chronicle page; used by buttons from Chronicle and the social graph."""
    events, page, pages = await _page(target.chat.id, mode, page, user_id, username)
    if not events:
        return False
    text = _format(target, events, target_label, mode=mode, page=page, pages=pages)
    markup = chronicle_keyboard(mode, page, pages, _encode_filter(user_id, username))
    if edit:
        await target.edit_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)
    else:
        await target.reply(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)
    return True


@router.callback_query(F.data.startswith(CALLBACK_PREFIX))
async def handle_chronicle_page(query: types.CallbackQuery):
    parts = (query.data or "").split(":", 3)
    if len(parts) != 4 or query.message is None or not config.CHRONICLE_ENABLED:
        await query.answer()
        return
    _prefix, mode, raw_page, raw_filter = parts
    try:
        page = int(raw_page)
    except ValueError:
        page = 0
    user_id, username = _decode_filter(raw_filter)
    label = f"@{username}" if username else ("участника" if user_id is not None else None)
    try:
        shown = await send_chronicle_page(
            query.message,
            mode=MODE_HALL if mode == MODE_HALL else MODE_RECENT,
            page=page,
            user_id=user_id,
            username=username,
            target_label=label,
            edit=True,
        )
    except Exception as exc:  # e.g. «message is not modified» on a double tap
        if "not modified" not in str(exc):
            raise
        shown = True
    await query.answer(None if shown else "Летопись пуста")


@router.message(lambda message: _command_kind(message) is not None)
async def handle_chronicle(message: types.Message):
    if not _is_group(message):
        await message.reply("Летопись живёт внутри групповых чатов, а не в одиночной камере.")
        return
    if not config.CHRONICLE_ENABLED:
        await message.reply("📜 Летопись сейчас отключена.")
        return

    backfill_state = await request_backfill(message.chat.id)
    kind, username = _command_kind(message) or ("chronicle", None)
    user_id: int | None = None
    target_label: str | None = None

    replied = getattr(message, "reply_to_message", None)
    replied_user = getattr(replied, "from_user", None) if replied else None
    if username:
        target_label = f"@{username}"
    elif replied_user and not replied_user.is_bot:
        user_id = int(replied_user.id)
        target_label = f"@{replied_user.username}" if replied_user.username else (replied_user.full_name or "этого гражданина")

    if kind == "hall":
        if not await send_chronicle_page(message, mode=MODE_HALL):
            await message.reply("🏆 Зал славы пока пуст: Летописи нечего туда повесить.")
        return

    events = await list_events(
        message.chat.id,
        user_id=user_id,
        username=username,
    )
    progress = _backfill_progress(backfill_state)
    if not events:
        if progress:
            subject = f" для {target_label}" if target_label else ""
            await message.reply(f"📜 Пока готовых событий{subject} нет.\n{progress}")
            return
        if backfill_state and backfill_state.get("status") == "completed":
            if target_label:
                await message.reply(
                    f"📜 Первичный просмотр доступной истории завершён. Для {target_label} ничего достаточно значимого не отобралось."
                )
            else:
                await message.reply(
                    "📜 Первичный просмотр всей доступной истории завершён, но достаточно сильных событий пока не нашлось. Новые события продолжаю ловить вживую."
                )
            return
        if target_label:
            await message.reply(
                "Летопись молчит. Пока что этот гражданин исторических преступлений не совершал."
            )
        else:
            await message.reply(
                "📜 Пока в летописи пусто. Я уже роюсь в старых протоколах чата и параллельно слежу за новыми преступлениями против здравого смысла."
            )
        return

    pages = 1
    if len(events) >= config.DISPLAY_EVENT_LIMIT:
        total = await count_events(message.chat.id, user_id=user_id, username=username)
        pages = max(1, -(-total // config.DISPLAY_EVENT_LIMIT))
    await message.reply(
        _format(message, events, target_label, progress, pages=pages),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=chronicle_keyboard(MODE_RECENT, 0, pages, _encode_filter(user_id, username)),
    )
