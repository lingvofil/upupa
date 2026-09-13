"""Accept an open DnD action as a reply to any message sent by this bot."""
from __future__ import annotations


def _event_text(event) -> str:
    return str(getattr(event, "text", None) or getattr(event, "caption", None) or "").strip()


def _reply_is_from_this_bot(event) -> bool:
    reply = getattr(event, "reply_to_message", None)
    author = getattr(reply, "from_user", None) if reply is not None else None
    if author is None or not bool(getattr(author, "is_bot", False)):
        return False

    bot = getattr(event, "bot", None)
    bot_id = getattr(bot, "id", None)
    author_id = getattr(author, "id", None)
    if bot_id is None or author_id is None:
        return True
    try:
        return int(bot_id) == int(author_id)
    except (TypeError, ValueError):
        return False


def is_any_bot_action_reply(event) -> bool:
    """Match old Upupa replies while leaving the current prompt to the canonical handler."""
    from AI import dnd
    from AI.dnd_state_commands import is_state_command

    chat = getattr(event, "chat", None)
    user = getattr(event, "from_user", None)
    if chat is None or user is None:
        return False

    session = dnd.dnd_sessions.get(int(chat.id))
    if not session or getattr(session, "state", None) != "WAITING_ACTION":
        return False

    prompt_message_id = int(getattr(session, "action_prompt_message_id", 0) or 0)
    reply = getattr(event, "reply_to_message", None)
    if not prompt_message_id or reply is None or not _reply_is_from_this_bot(event):
        return False

    # The existing DnD route already handles the current action prompt. This
    # compatibility route is only for older/other messages sent by Upupa.
    try:
        if int(getattr(reply, "message_id", 0) or 0) == prompt_message_id:
            return False
    except (TypeError, ValueError):
        return False

    action = _event_text(event)
    if not action:
        return False
    normalized = action.casefold()
    if normalized == "дальше" or normalized.startswith("упупа") or is_state_command(action):
        return False

    try:
        user_id = int(user.id)
    except (TypeError, ValueError):
        return False
    return bool(
        dnd._can_user_act(
            session,
            user_id,
            getattr(session, "action_target_user_ids", []) or [],
        )
    )


async def handle_any_bot_action_reply(message) -> None:
    """Delegate to the canonical action collector after the relaxed reply filter matched."""
    from AI import dnd

    await dnd.handle_free_action(message)


def configure_dnd_any_bot_replies(dnd_router) -> None:
    """Register the relaxed reply route once, after ordinary DnD/state routes."""
    if getattr(dnd_router, "_upupa_dnd_any_bot_reply_configured", False):
        return
    dnd_router.message.register(handle_any_bot_action_reply, is_any_bot_action_reply)
    dnd_router._upupa_dnd_any_bot_reply_configured = True
