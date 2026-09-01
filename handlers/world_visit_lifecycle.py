"""Lifecycle guard for state visits: 24h expiry, showcase replies and manual finish."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import re

from aiogram import F, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.loader import bot
from features.world.interactions import (
    finish_visit,
    get_open_visit,
    notify_visit_finished,
    record_interaction_event,
    visit_is_active,
)
from features.world.permissions import is_chat_admin
from features.world.service import get_world_service
from features.world.visit_feedback import (
    close_feedback_window,
    get_feedback_window_for_prompt,
    record_visit_feedback,
)
from handlers.world import _require_world, _title


router = Router(name="world_visit_lifecycle")
VISIT_PROMPT_PREFIX = "🛬 Государственный визит"
FEEDBACK_PROMPT_PREFIX = "📝 Отзывы об екскурсии"
_VISIT_TARGET_RE = re.compile(r"Гости: государство №(\d+)")


def _is_visit_showcase_reply(message: types.Message) -> bool:
    replied = message.reply_to_message
    if replied is None or message.from_user is None:
        return False
    content = (message.text or message.caption or "").strip()
    replied_text = (replied.text or replied.caption or "").strip()
    return bool(content and replied_text.startswith(VISIT_PROMPT_PREFIX))


def _is_visit_feedback_reply(message: types.Message) -> bool:
    replied = message.reply_to_message
    if replied is None or message.from_user is None:
        return False
    content = (message.text or message.caption or "").strip()
    replied_text = (replied.text or replied.caption or "").strip()
    return bool(content and replied_text.startswith(FEEDBACK_PROMPT_PREFIX))


def _visit_markup(guest_world_id: int) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🏁 Завершить визит",
        callback_data=f"worldvisitend:{guest_world_id}",
    )
    return builder.as_markup()


async def _close_expired_if_needed(service, host_world_id: int, guest_world_id: int):
    visit = await get_open_visit(service, host_world_id, guest_world_id)
    if visit is None or visit_is_active(visit):
        return visit
    closed = await finish_visit(
        service,
        host_world_id,
        guest_world_id,
        reason="timeout",
    )
    if closed is not None:
        await notify_visit_finished(bot, service, closed, reason="timeout")
    return None


@router.callback_query(F.data.startswith("worldvisit:"))
async def world_visit_decision_callback(query: types.CallbackQuery):
    """Accept/reject visit invitations and start a bounded 24-hour visit."""
    if query.message is None or query.from_user is None or not query.data:
        return
    if not await is_chat_admin(bot, query.message.chat.id, query.from_user.id):
        await query.answer(
            "Решать, едет ли государство в гости, могут администраторы или посол.",
            show_alert=True,
        )
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Кривое приглашение.", show_alert=True)
        return
    try:
        host_world_id = int(parts[1])
    except ValueError:
        await query.answer("Кривое приглашение.", show_alert=True)
        return
    decision = parts[2]
    if decision not in {"accept", "reject"}:
        await query.answer("Неизвестный ответ на приглашение.", show_alert=True)
        return

    service = get_world_service()
    guest = await service.get_state(
        query.message.chat.id,
        query.message.chat.title or "Безымянное государство",
    )
    host = await service.get_state_by_world_id(host_world_id)
    if guest is None or host is None or not host.enabled:
        await query.answer("Одно из государств уже недоступно.", show_alert=True)
        return
    if guest.world_id == host.world_id:
        await query.answer("Это приглашение явно попало не туда.", show_alert=True)
        return

    if decision == "accept":
        active = await _close_expired_if_needed(service, host.world_id, guest.world_id)
        if active is not None:
            await query.answer("Этот государственный визит уже идёт.", show_alert=True)
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            return

    event_type = "state_visit_accepted" if decision == "accept" else "state_visit_rejected"
    recorded = await record_interaction_event(
        service,
        event_type,
        actor_state=guest.world_id,
        target_state=host.world_id,
        payload={"answered_by": query.from_user.full_name},
        dedupe_key=f"visit_decision:{guest.world_id}:{query.message.message_id}",
    )
    if service.ledger is not None and not recorded:
        await query.answer("Это приглашение уже обработано.", show_alert=True)
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if decision == "reject":
        await query.answer("Визит отклонён")
        await query.message.answer("🙅 Решено никуда не ехать. Международный туризм отменяется.")
        try:
            await bot.send_message(
                host.chat_id,
                f"🙅 Государство №{guest.world_id} — {guest.title} отказалось от визита.",
            )
        except Exception:
            logging.exception("World visit rejection notification failed host=%s", host.world_id)
        return

    await query.answer("Едем")
    await query.message.answer(
        f"🛬 Приглашение принято. Делегация отправляется в государство №{host.world_id} — {host.title}. "
        "На всё про всё — 24 часа."
    )
    try:
        await bot.send_message(
            host.chat_id,
            f"{VISIT_PROMPT_PREFIX}\n\n"
            f"Гости: государство №{guest.world_id} — {guest.title}.\n"
            "Они приехали смотреть, чем вы тут вообще живёте. Екскурсия продлится максимум 24 часа.\n\n"
            "Любой участник чата может ответить на это сообщение текстом и рассказать, что именно показывает гостям. "
            "Упупа подпишет автора и передаст это делегации.",
            reply_markup=_visit_markup(guest.world_id),
        )
    except Exception:
        logging.exception("World visit host prompt failed host=%s", host.world_id)


@router.callback_query(F.data.startswith("worldvisitend:"))
async def finish_world_visit_callback(query: types.CallbackQuery):
    """Let a host-state admin or ambassador end the current visit early."""
    if query.message is None or query.from_user is None or not query.data:
        return
    if not await is_chat_admin(bot, query.message.chat.id, query.from_user.id):
        await query.answer(
            "Завершать государственный визит могут администраторы или посол.",
            show_alert=True,
        )
        return
    try:
        guest_world_id = int(query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.answer("Кривой визит.", show_alert=True)
        return

    service = get_world_service()
    host = await service.get_state(
        query.message.chat.id,
        query.message.chat.title or "Безымянное государство",
    )
    if host is None:
        await query.answer("Государство не найдено.", show_alert=True)
        return
    visit = await get_open_visit(service, host.world_id, guest_world_id)
    if visit is None:
        await query.answer("Этот визит уже завершён.", show_alert=True)
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    reason = "manual" if visit_is_active(visit) else "timeout"
    closed = await finish_visit(
        service,
        host.world_id,
        guest_world_id,
        reason=reason,
        finished_by=query.from_user.full_name,
    )
    if closed is None:
        await query.answer("Этот визит уже завершён.", show_alert=True)
        return
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await notify_visit_finished(bot, service, closed, reason=reason)
    await query.answer("Визит завершён")


@router.message(_is_visit_feedback_reply)
async def visit_feedback_reply(message: types.Message):
    """Accept guest-state feedback for one hour after a completed екскурсия."""
    service = await _require_world(message)
    if service is None or message.from_user is None or message.reply_to_message is None:
        return

    guest = await service.get_state(message.chat.id, _title(message))
    if guest is None:
        return
    window = await get_feedback_window_for_prompt(
        service,
        guest_state=guest.world_id,
        prompt_message_id=message.reply_to_message.message_id,
    )
    if window is None:
        await message.reply("📝 Не нашёл это окно отзывов. Возможно, дипломатическая бумажка потерялась.")
        return
    if window.closed:
        await message.reply("📝 Отзывы уже собраны и отправлены. Поздняк метаться.")
        return

    now = datetime.now(timezone.utc)
    if now >= window.closes_at:
        await close_feedback_window(bot, service, window, now=now)
        await message.reply("📝 Час на отзывы уже закончился. Этот отзыв в протокол не попал.")
        return

    content = " ".join((message.text or message.caption or "").split()).strip()[:1000]
    if not content:
        return
    name = message.from_user.full_name or (
        f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    )
    recorded = await record_visit_feedback(
        service,
        window,
        message_id=message.message_id,
        user_id=message.from_user.id,
        user_name=name,
        text=content,
    )
    if recorded:
        await message.reply("📝 Отзыв принят. Через час Упупа отнесёт его принимающей стороне.")
    else:
        await message.reply("📝 Этот отзыв уже записан.")


@router.message(_is_visit_showcase_reply)
async def visit_showcase(message: types.Message):
    """Accept showcase replies only while the corresponding visit is active."""
    service = await _require_world(message)
    if service is None or message.from_user is None or message.reply_to_message is None:
        return
    replied_text = message.reply_to_message.text or message.reply_to_message.caption or ""
    match = _VISIT_TARGET_RE.search(replied_text)
    if match is None:
        return
    guest_world_id = int(match.group(1))
    host = await service.get_state(message.chat.id, _title(message))
    guest = await service.get_state_by_world_id(guest_world_id)
    if host is None or guest is None or not guest.enabled:
        await message.reply("🎒 Делегация уже куда-то уехала. Показывать некому.")
        return

    visit = await _close_expired_if_needed(service, host.world_id, guest.world_id)
    if visit is None:
        await message.reply("🎒 Екскурсия уже закончилась. Делегация уехала, поздно показывать достопримечательности.")
        try:
            await bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=message.reply_to_message.message_id,
                reply_markup=None,
            )
        except Exception:
            pass
        return

    content = " ".join((message.text or message.caption or "").split()).strip()[:1000]
    if not content:
        return
    name = message.from_user.full_name or (
        f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    )
    await record_interaction_event(
        service,
        "state_visit_showcase",
        actor_state=host.world_id,
        target_state=guest.world_id,
        payload={
            "user_id": message.from_user.id,
            "user_name": name,
            "text": content,
        },
    )
    await message.reply(f"🎒 {name} показал гостям: {content}")
    try:
        await bot.send_message(
            guest.chat_id,
            f"🎒 Екскурсия по государству №{host.world_id} — {host.title}.\n\n"
            f"{name} показал вам: {content}",
        )
    except Exception:
        logging.exception("World visit showcase delivery failed guest=%s", guest.world_id)
