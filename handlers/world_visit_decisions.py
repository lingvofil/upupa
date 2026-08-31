"""Incoming state-visit decisions for World of Upupa."""

from __future__ import annotations

from aiogram import F, Router, types

from core.loader import bot
from features.world.permissions import is_chat_admin
from handlers.world_interactions import _handle_visit_decision


router = Router(name="world_visit_decisions")


@router.callback_query(F.data.startswith("worldvisit:"))
async def world_visit_decision_callback(query: types.CallbackQuery):
    """Only an admin or the appointed ambassador may accept/reject a visit."""
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
        source_world_id = int(parts[1])
    except ValueError:
        await query.answer("Кривое приглашение.", show_alert=True)
        return

    await _handle_visit_decision(query, source_world_id, parts[2])
