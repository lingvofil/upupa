"""Accept DnD replies to any message sent by this bot where that is unambiguous."""
from __future__ import annotations

import logging
import re

from features.song.command_guard import is_song_command


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
    if not action or is_song_command(event):
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


def is_any_bot_backstory_reply(event) -> bool:
    """Accept a host's custom backstory as a reply to any Upupa message.

    The exact backstory prompt remains owned by the canonical route in ``dnd.py``.
    This fallback covers the easy-to-hit case where the host replies to the nearby
    ``🎲 Своя предыстория.`` message (or another Upupa message) instead.
    """
    from AI import dnd

    chat = getattr(event, "chat", None)
    user = getattr(event, "from_user", None)
    if chat is None or user is None:
        return False

    chat_id = int(chat.id)
    session = dnd.dnd_sessions.get(chat_id)
    if (
        not session
        or getattr(session, "state", None) != "WAITING_BACKSTORY"
        or chat_id in dnd._processing_backstories
    ):
        return False

    reply = getattr(event, "reply_to_message", None)
    if reply is None or not _reply_is_from_this_bot(event) or not _event_text(event):
        return False
    if is_song_command(event):
        return False

    try:
        user_id = int(user.id)
    except (TypeError, ValueError):
        return False
    starter_user_id = getattr(session, "starter_user_id", None)
    if starter_user_id is not None and not dnd._user_is_host(session, user_id):
        return False

    # The exact prompt is still handled by the original backstory filter. Keeping
    # that path canonical avoids double handling while relaxing only other bot replies.
    prompt_message_id = int(getattr(session, "backstory_prompt_message_id", 0) or 0)
    try:
        reply_message_id = int(getattr(reply, "message_id", 0) or 0)
    except (TypeError, ValueError):
        return False
    return not prompt_message_id or reply_message_id != prompt_message_id


def is_any_bot_poll_reply(event) -> bool:
    """Do not silently lose replies to Upupa while a participant poll is open."""
    from AI import dnd
    from AI.dnd_state_commands import is_state_command

    chat = getattr(event, "chat", None)
    user = getattr(event, "from_user", None)
    if chat is None or user is None:
        return False

    session = dnd.dnd_sessions.get(int(chat.id))
    if (
        not session
        or getattr(session, "state", None) != "WAITING_POLL"
        or getattr(session, "mode", None) != "participants"
        or not getattr(session, "pending_poll", None)
        or not _reply_is_from_this_bot(event)
    ):
        return False

    text = _event_text(event)
    if not text or is_song_command(event):
        return False
    normalized = text.casefold()
    if normalized == "дальше" or normalized.startswith("упупа") or is_state_command(text):
        return False

    try:
        user_id = int(user.id)
    except (TypeError, ValueError):
        return False
    return user_id in dnd._participant_ids(session)


def _normalized_option(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _poll_option_index(text: str, options: list[str]) -> int | None:
    normalized = _normalized_option(text).strip(" .):-")
    number_match = re.fullmatch(r"(?:вариант\s*)?(\d+)", normalized)
    if number_match:
        index = int(number_match.group(1)) - 1
        return index if 0 <= index < len(options) else None

    for index, option in enumerate(options):
        if normalized == _normalized_option(option):
            return index
    return None


def _expected_poll_ids(dnd, session) -> set[int]:
    poll = getattr(session, "pending_poll", None) or {}
    participants = set(dnd._participant_ids(session))
    targets = {int(value) for value in poll.get("target_user_ids") or []}
    expected = participants & targets if targets else participants

    middleware = getattr(dnd.dnd_router, "_upupa_dnd_completion_middleware", None)
    policy = getattr(middleware, "policy", None)
    filter_expected_ids = getattr(policy, "filter_expected_ids", None)
    if filter_expected_ids is not None:
        expected = set(filter_expected_ids(dnd, session, expected))
    return expected


async def handle_any_bot_action_reply(message) -> None:
    """Delegate to the canonical action collector after the relaxed reply filter matched."""
    from AI import dnd

    await dnd.handle_free_action(message)


async def handle_any_bot_backstory_reply(message) -> None:
    """Delegate a relaxed custom-backstory reply to the canonical backstory handler."""
    from AI import dnd

    await dnd.handle_backstory(message)


async def handle_any_bot_poll_reply(message) -> None:
    """Accept textual poll choices or explicitly tell the player what state is active."""
    from AI import dnd

    session = dnd.dnd_sessions.get(int(message.chat.id))
    if not session or session.state != "WAITING_POLL" or not session.pending_poll:
        return

    user_id = int(message.from_user.id)
    poll = session.pending_poll
    options = list(poll.get("options") or [])
    if not dnd._poll_user_is_eligible(session, user_id):
        await message.answer("🗳 Реплай вижу, но это голосование сейчас не для твоего персонажа.")
        return

    option_index = _poll_option_index(_event_text(message), options)
    if option_index is None:
        await message.answer(
            "🗳 Реплай вижу. Сейчас идёт голосование: нажми вариант в опросе "
            "или ответь мне номером варианта, например «2»."
        )
        return

    poll.setdefault("votes", {})[str(user_id)] = option_index
    dnd.persist_dnd_sessions()
    logging.info(
        "DnD text poll vote chat_id=%s poll_id=%s user_id=%s option=%s",
        message.chat.id,
        getattr(session, "current_poll_id", None),
        user_id,
        option_index,
    )
    await message.answer(f"🗳 Голос учтён: {options[option_index]}")

    expected = _expected_poll_ids(dnd, session)
    voted = {int(value) for value in (poll.get("votes") or {}).keys() if str(value).lstrip("-").isdigit()}
    if expected and expected.issubset(voted):
        await dnd.finalize_poll(
            message.bot,
            int(message.chat.id),
            int(poll["message_id"]),
            options,
        )


def configure_dnd_any_bot_replies(dnd_router) -> None:
    """Register relaxed DnD reply routes once, after ordinary DnD/state routes."""
    if getattr(dnd_router, "_upupa_dnd_any_bot_reply_configured", False):
        return
    dnd_router.message.register(handle_any_bot_action_reply, is_any_bot_action_reply)
    dnd_router.message.register(handle_any_bot_backstory_reply, is_any_bot_backstory_reply)
    dnd_router.message.register(handle_any_bot_poll_reply, is_any_bot_poll_reply)
    dnd_router._upupa_dnd_any_bot_reply_configured = True
