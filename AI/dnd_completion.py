"""Auto-finalize DnD participant actions and polls when everyone eligible has responded."""

from __future__ import annotations

import logging
import time

from aiogram import BaseMiddleware


DND_PARTICIPANT_CONTEXT_MARKER = "ТЕКУЩИЕ УЧАСТНИКИ ПАРТИИ"


def _with_participant_context(dnd, session, prompt: str) -> str:
    """Keep the model aware of late-joining participant IDs and names."""
    if not dnd._is_participant_mode(session):
        return prompt
    participants = list((getattr(session, "participants", {}) or {}).values())
    if not participants:
        return prompt
    roster = "\n".join(
        f"- ID {int(item['user_id'])}: {item.get('name') or f'егрок {int(item[\"user_id\"])}'}"
        for item in participants
        if item.get("user_id") is not None
    )
    if not roster:
        return prompt
    return (
        f"{prompt}\n\n"
        f"{DND_PARTICIPANT_CONTEXT_MARKER}:\n{roster}\n"
        "Это актуальный состав: участники могли влиться в егру уже после старта. "
        "Считай всех из этого списка полноценными персонажами и используй их ID в TARGETS, "
        "когда ход относится к конкретным людям."
    )


class DndParticipantCompletionMiddleware(BaseMiddleware):
    """Collect participant replies robustly and finish complete group decisions early."""

    async def __call__(self, handler, event, data):
        from AI import dnd

        bot = data.get("bot")
        try:
            if bot is not None and getattr(event, "poll_id", None) is None:
                await self._precollect_action_reply(dnd, bot, event)
        except Exception:
            logging.exception("DnD participant precollect failed event=%r", event)

        result = await handler(event, data)
        try:
            if bot is not None:
                await self._maybe_finalize(event, bot, dnd)
        except Exception:
            logging.exception("DnD participant auto-finalize failed event=%r", event)
        return result

    @staticmethod
    def _expected_ids(dnd, session, target_user_ids) -> set[int]:
        if not dnd._is_participant_mode(session):
            return set()
        participants = dnd._participant_ids(session)
        targets = {int(value) for value in (target_user_ids or [])}
        return participants & targets if targets else participants

    async def _precollect_action_reply(self, dnd, bot, event) -> None:
        chat = getattr(event, "chat", None)
        user = getattr(event, "from_user", None)
        if chat is None or user is None:
            return
        chat_id = int(chat.id)
        session = dnd.dnd_sessions.get(chat_id)
        if (
            not session
            or session.state != "WAITING_ACTION"
            or not dnd._is_participant_mode(session)
        ):
            return

        prompt_message_id = int(getattr(session, "action_prompt_message_id", 0) or 0)
        reply = getattr(event, "reply_to_message", None)
        if not prompt_message_id or reply is None:
            return
        if int(getattr(reply, "message_id", 0) or 0) != prompt_message_id:
            return

        action = getattr(event, "text", None) or getattr(event, "caption", None)
        if not action:
            return
        normalized = str(action).strip().casefold()
        if normalized == "дальше" or normalized.startswith("упупа"):
            return

        user_id = int(user.id)
        participants = dnd._participant_ids(session)
        targets = list(getattr(session, "action_target_user_ids", []) or [])
        user_name = getattr(user, "first_name", None) or f"егрок {user_id}"

        if user_id not in participants:
            if targets:
                expected = self._expected_ids(dnd, session, targets)
                names = ", ".join(dnd._target_names(session, sorted(expected)))
                logging.info(
                    "DnD late participant reply rejected: targeted turn chat_id=%s user_id=%s expected_ids=%s",
                    chat_id,
                    user_id,
                    sorted(expected),
                )
                await bot.send_message(
                    chat_id,
                    f"{user_name}, щас не влезай: эта движуха для {names or 'других егроков'}.",
                )
                return

            session.participants[str(user_id)] = {
                "user_id": user_id,
                "name": user_name,
            }
            participants.add(user_id)
            logging.info(
                "DnD late participant joined through open action chat_id=%s user_id=%s name=%s",
                chat_id,
                user_id,
                user_name,
            )

        expected = self._expected_ids(dnd, session, targets)
        if expected and user_id not in expected:
            names = ", ".join(dnd._target_names(session, sorted(expected)))
            logging.info(
                "DnD participant reply rejected: not targeted chat_id=%s user_id=%s expected_ids=%s",
                chat_id,
                user_id,
                sorted(expected),
            )
            await bot.send_message(
                chat_id,
                f"{user_name}, твой ход щас не учтён: эта движуха для {names}.",
            )
            return

        session.pending_actions[str(user_id)] = {
            "user_id": user_id,
            "name": user_name,
            "action": str(action),
        }
        dnd.persist_dnd_sessions()
        logging.info(
            "DnD participant action precollected chat_id=%s user_id=%s prompt_message_id=%s",
            chat_id,
            user_id,
            prompt_message_id,
        )

    async def _maybe_finalize(self, event, bot, dnd) -> None:
        poll_id = getattr(event, "poll_id", None)
        if poll_id is not None:
            await self._maybe_finalize_poll(dnd, bot, str(poll_id))
            return

        chat = getattr(event, "chat", None)
        if chat is None:
            return
        chat_id = int(chat.id)
        complete = await self._maybe_finalize_action(dnd, bot, chat_id)
        if not complete:
            await self._ensure_action_timer(dnd, bot, chat_id)

    async def _maybe_finalize_action(self, dnd, bot, chat_id: int) -> bool:
        session = dnd.dnd_sessions.get(chat_id)
        if not session or session.state != "WAITING_ACTION":
            return False

        prompt_message_id = int(getattr(session, "action_prompt_message_id", 0) or 0)
        if not prompt_message_id:
            return False

        expected = self._expected_ids(
            dnd,
            session,
            getattr(session, "action_target_user_ids", []) or [],
        )
        if not expected:
            return False

        submitted = set()
        for key, value in (getattr(session, "pending_actions", {}) or {}).items():
            raw_id = value.get("user_id") if isinstance(value, dict) else key
            try:
                submitted.add(int(raw_id))
            except (TypeError, ValueError):
                continue

        logging.info(
            "DnD participant action progress chat_id=%s submitted=%s expected=%s submitted_ids=%s expected_ids=%s",
            chat_id,
            len(submitted & expected),
            len(expected),
            sorted(submitted),
            sorted(expected),
        )
        if not expected.issubset(submitted):
            return False

        logging.info(
            "DnD participant action auto-finalize chat_id=%s prompt_message_id=%s",
            chat_id,
            prompt_message_id,
        )
        await dnd.finalize_group_actions(bot, chat_id, prompt_message_id)
        return True

    async def _ensure_action_timer(self, dnd, bot, chat_id: int) -> None:
        session = dnd.dnd_sessions.get(chat_id)
        if (
            not session
            or session.state != "WAITING_ACTION"
            or not dnd._is_participant_mode(session)
            or getattr(session, "action_deadline", None) is not None
            or not (getattr(session, "pending_actions", {}) or {})
        ):
            return
        prompt_message_id = int(getattr(session, "action_prompt_message_id", 0) or 0)
        if not prompt_message_id:
            return

        session.action_deadline = time.time() + dnd.DND_ACTION_WINDOW_SECONDS
        dnd.persist_dnd_sessions()
        dnd._start_background_task(
            dnd.wait_for_action_timeout(bot, chat_id, prompt_message_id),
            name=f"dnd-actions:{chat_id}:{prompt_message_id}:completion-fallback",
        )
        logging.warning(
            "DnD participant action timer recovered chat_id=%s prompt_message_id=%s",
            chat_id,
            prompt_message_id,
        )

    async def _maybe_finalize_poll(self, dnd, bot, poll_id: str) -> None:
        chat_id = dnd.poll_map.get(poll_id)
        if chat_id is None:
            return
        session = dnd.dnd_sessions.get(chat_id)
        if not session or session.state != "WAITING_POLL":
            return
        poll = getattr(session, "pending_poll", None) or {}
        if str(getattr(session, "current_poll_id", "") or "") != poll_id:
            return

        expected = self._expected_ids(
            dnd,
            session,
            poll.get("target_user_ids") or [],
        )
        if not expected:
            return

        voted = set()
        for key in (poll.get("votes") or {}):
            try:
                voted.add(int(key))
            except (TypeError, ValueError):
                continue

        logging.info(
            "DnD participant poll progress chat_id=%s poll_id=%s voted=%s expected=%s voted_ids=%s expected_ids=%s",
            chat_id,
            poll_id,
            len(voted & expected),
            len(expected),
            sorted(voted),
            sorted(expected),
        )
        if expected.issubset(voted):
            logging.info(
                "DnD participant poll auto-finalize chat_id=%s poll_id=%s",
                chat_id,
                poll_id,
            )
            await dnd.finalize_poll(
                bot,
                chat_id,
                int(poll["message_id"]),
                list(poll["options"]),
            )


def configure_dnd_completion(dnd_router) -> None:
    """Attach participant completion middleware and live participant context once."""
    if getattr(dnd_router, "_upupa_dnd_completion_configured", False):
        return

    from AI import dnd

    original_generate_session_response = dnd.generate_session_response

    async def generate_with_participant_context(session, prompt: str) -> str:
        return await original_generate_session_response(
            session,
            _with_participant_context(dnd, session, prompt),
        )

    dnd.generate_session_response = generate_with_participant_context

    middleware = DndParticipantCompletionMiddleware()
    dnd_router.message.outer_middleware(middleware)
    dnd_router.poll_answer.outer_middleware(middleware)
    dnd_router._upupa_dnd_completion_configured = True
