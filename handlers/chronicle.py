"""Telegram UX for the chat Chronicle."""

from __future__ import annotations

from html import escape
import re
from zoneinfo import ZoneInfo

from aiogram import Router, types

from core.settings import APP_TIMEZONE_NAME
from features.chronicle import config
from features.chronicle.runtime import list_events, request_backfill


router = Router(name="chronicle")
_USER_RE = re.compile(r"^летопись\s+@([A-Za-z0-9_]{3,64})$", re.IGNORECASE)


def _command_kind(message: types.Message) -> tuple[str, str | None] | None:
    text = (message.text or "").strip()
    if text.casefold() == "летопись":
        return "chronicle", None
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


def _format(message: types.Message, events, target_label: str | None) -> str:
    header = "📜 <b>Летопись чата</b>" if target_label is None else f"📜 <b>Летопись {escape(target_label)}</b>"
    lines = [header]
    for event in events:
        lines.append("")
        lines.append(f"<b>{_event_date(event.event_started_at)} — {escape(event.title)}</b>")
        lines.append(escape(event.summary))
        url = _message_url(message, event.anchor_message_id)
        if url:
            lines.append(f'<a href="{escape(url, quote=True)}">↗ к месту преступления</a>')
    return "\n".join(lines)


@router.message(lambda message: _command_kind(message) is not None)
async def handle_chronicle(message: types.Message):
    if not _is_group(message):
        await message.reply("Летопись живёт внутри групповых чатов, а не в одиночной камере.")
        return
    if not config.CHRONICLE_ENABLED:
        await message.reply("📜 Летопись сейчас отключена.")
        return

    await request_backfill(message.chat.id)
    _kind, username = _command_kind(message) or ("chronicle", None)
    user_id: int | None = None
    target_label: str | None = None

    replied = getattr(message, "reply_to_message", None)
    replied_user = getattr(replied, "from_user", None) if replied else None
    if username:
        target_label = f"@{username}"
    elif replied_user and not replied_user.is_bot:
        user_id = int(replied_user.id)
        target_label = f"@{replied_user.username}" if replied_user.username else (replied_user.full_name or "этого гражданина")

    events = await list_events(
        message.chat.id,
        user_id=user_id,
        username=username,
    )
    if not events:
        if target_label:
            await message.reply(
                "Летопись молчит. Пока что этот гражданин исторических преступлений не совершал."
            )
        else:
            await message.reply(
                "📜 Пока в летописи пусто. Я уже роюсь в старых протоколах чата и параллельно слежу за новыми преступлениями против здравого смысла."
            )
        return

    await message.reply(
        _format(message, events, target_label),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
