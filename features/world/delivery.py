"""Cross-chat delivery for World of Upupa with migration and dead-chat handling.

Every message that one state sends into another state's chat goes through
here.  Telegram tells us when a group was upgraded to a supergroup
(``TelegramMigrateToChat``) — the state follows the new chat id and the send is
retried once.  When the bot was kicked or the chat no longer exists, the state
is switched off so the World stops writing into the void.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
)

from features.world.models import WorldState


_DEAD_CHAT_MARKERS = (
    "chat not found",
    "group chat was deactivated",
    "group chat was upgraded",
    "bot was kicked",
    "bot is not a member",
)


def _is_dead_chat(error: Exception) -> bool:
    if isinstance(error, TelegramForbiddenError):
        return True
    message = str(getattr(error, "message", "") or error).lower()
    return isinstance(error, TelegramBadRequest) and any(marker in message for marker in _DEAD_CHAT_MARKERS)


async def _send(
    service,
    state: WorldState,
    action: Callable[[int], Awaitable[Any]],
    *,
    what: str,
) -> Any | None:
    chat_id = state.chat_id
    for attempt in range(2):
        try:
            return await action(chat_id)
        except TelegramMigrateToChat as error:
            new_chat_id = int(error.migrate_to_chat_id)
            migrated = await service.migrate_chat(chat_id, new_chat_id) if service is not None else None
            logging.info("World delivery follows chat migration %s -> %s", chat_id, new_chat_id)
            chat_id = migrated.chat_id if migrated is not None else new_chat_id
            if attempt == 0:
                continue
            return None
        except Exception as error:  # noqa: BLE001 - delivery must never break the caller
            if _is_dead_chat(error) and service is not None:
                logging.warning(
                    "World %s to state %s failed permanently (%s); disabling state",
                    what,
                    state.world_id,
                    error,
                )
                try:
                    await service.disable_state(chat_id, reason="unreachable")
                except Exception:
                    logging.exception("World auto-disable failed state=%s", state.world_id)
            else:
                logging.exception("World %s to state %s failed", what, state.world_id)
            return None
    return None


async def deliver_text(bot, service, state: WorldState | None, text: str, **kwargs) -> Any | None:
    if state is None or not state.enabled:
        return None
    return await _send(
        service,
        state,
        lambda chat_id: bot.send_message(chat_id, text, **kwargs),
        what="message",
    )


async def deliver_copy(bot, service, state: WorldState | None, *, from_chat_id: int, message_id: int) -> Any | None:
    if state is None or not state.enabled:
        return None
    return await _send(
        service,
        state,
        lambda chat_id: bot.copy_message(chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id),
        what="copy",
    )
