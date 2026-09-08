"""Auto-finalize DnD participant actions and polls when everyone eligible has responded."""

from __future__ import annotations

import logging

from aiogram import BaseMiddleware


class DndParticipantCompletionMiddleware(BaseMiddleware):
    """Finish participant-mode group decisions as soon as all eligible users answered."""

    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        try:
            await self._maybe_finalize(event, data)
        except Exception:
            logging.exception("DnD participant auto-finalize failed event=%r", event)
        return result

    async def _maybe_finalize(self, event, data) -> None:
        from AI import dnd

        bot = data.get("bot")
        if bot is None:
            return

        poll_id = getattr(event, "poll_id", None)
        if poll_id is not None:
            await self._maybe_finalize_poll(dnd, bot, str(poll_id))
            return

        chat = getattr(event, "chat", None)
        if chat is None:
            return
        await self._maybe_finalize_action(dnd, bot, int(chat.id))

    @staticmethod
    def _expected_ids(dnd, session, target_user_ids) -> set[int]:
        if not dnd._is_participant_mode(session):
            return set()
        participants = dnd._participant_ids(session)
        targets = {int(value) for value in (target_user_ids or [])}
        return participants & targets if targets else participants

    async def _maybe_finalize_action(self, dnd, bot, chat_id: int) -> None:
        session = dnd.dnd_sessions.get(chat_id)
        if not session or session.state != "WAITING_ACTION":
            return

        prompt_message_id = int(getattr(session, "action_prompt_message_id", 0) or 0)
        if not prompt_message_id:
            return

        expected = self._expected_ids(
            dnd,
            session,
            getattr(session, "action_target_user_ids", []) or [],
        )
        if not expected:
            return

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
        if expected.issubset(submitted):
            logging.info(
                "DnD participant action auto-finalize chat_id=%s prompt_message_id=%s",
                chat_id,
                prompt_message_id,
            )
            await dnd.finalize_group_actions(bot, chat_id, prompt_message_id)

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
    """Attach participant completion middleware once."""
    if getattr(dnd_router, "_upupa_dnd_completion_configured", False):
        return
    middleware = DndParticipantCompletionMiddleware()
    dnd_router.message.outer_middleware(middleware)
    dnd_router.poll_answer.outer_middleware(middleware)
    dnd_router._upupa_dnd_completion_configured = True
