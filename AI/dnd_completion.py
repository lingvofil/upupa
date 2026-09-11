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
    roster_lines = []
    for item in participants:
        if item.get("user_id") is None:
            continue
        user_id = int(item["user_id"])
        name = item.get("name") or f"егрок {user_id}"
        roster_lines.append(f"- ID {user_id}: {name}")
    if not roster_lines:
        return prompt
    roster = "\n".join(roster_lines)
    return (
        f"{prompt}\n\n"
        f"{DND_PARTICIPANT_CONTEXT_MARKER}:\n{roster}\n"
        "Это актуальный состав: участники могли влиться в егру уже после старта. "
        "Считай всех из этого списка полноценными персонажами и используй их ID в TARGETS, "
        "когда ход относится к конкретным людям."
    )


def _refresh_gemini_chat_session(dnd, session) -> None:
    """Rebuild Gemini state from DnD's canonical conversation before every turn."""
    if getattr(session, "active_model", None) != "gemini":
        return

    history = []
    for item in getattr(session, "conversation", None) or []:
        if not isinstance(item, dict):
            continue
        source_role = item.get("role")
        content = item.get("content")
        if content is None:
            continue
        if source_role in {"assistant", "model"}:
            role = "model"
        elif source_role == "user":
            role = "user"
        else:
            logging.warning(
                "DnD dropped invalid Gemini history role chat_id=%s role=%r",
                getattr(session, "chat_id", None),
                source_role,
            )
            continue
        history.append({"role": role, "parts": [str(content)]})

    session.chat_session = dnd.model.start_chat(
        chat_id=session.chat_id,
        history=history,
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
        from AI.dnd_state_commands import is_state_command
        if is_state_command(action):
            return

        user_id = int(user.id)
        participants = dnd._participant_ids(session)
        targets = list(getattr(session, "action_target_user_ids", []) or [])
        user_name = getattr(user, "first_name", None) or f"егрок {user_id}"

        if user_id not in participants:
            session.participants[str(user_id)] = {
                "user_id": user_id,
                "name": user_name,
            }
            participants.add(user_id)
            dnd.persist_dnd_sessions()
            logging.info(
                "DnD late participant joined chat_id=%s user_id=%s name=%s targeted=%s",
                chat_id,
                user_id,
                user_name,
                bool(targets),
            )

            if targets:
                expected = self._expected_ids(dnd, session, targets)
                names = ", ".join(dnd._target_names(session, sorted(expected)))
                await bot.send_message(
                    chat_id,
                    f"{user_name}, ты влез в егру. Но щас ход {names or 'других егроков'}: "
                    "твой ответ не учтён — жди следующей движухи.",
                )
                return

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
    """Attach participant completion middleware and DnD generation guards once."""
    if getattr(dnd_router, "_upupa_dnd_completion_configured", False):
        return

    from AI import dnd
    from AI.dnd_campaign import configure_dnd_campaign
    from AI.dnd_inventory_fun import install_fun_inventory
    from AI.dnd_state_commands import configure_dnd_state_commands

    install_fun_inventory()
    original_generate_session_response = dnd.generate_session_response

    async def generate_with_participant_context(session, prompt: str) -> str:
        _refresh_gemini_chat_session(dnd, session)
        return await original_generate_session_response(
            session,
            _with_participant_context(dnd, session, prompt),
        )

    dnd.generate_session_response = generate_with_participant_context

    # State queries and the short start alias must run before action collection,
    # otherwise a reply like «Мой герой» can become an in-game move.
    configure_dnd_state_commands(dnd_router)
    middleware = DndParticipantCompletionMiddleware()
    dnd_router.message.outer_middleware(middleware)
    dnd_router.poll_answer.outer_middleware(middleware)
    configure_dnd_campaign(dnd, dnd_router)

    campaign_generate_session_response = dnd.generate_session_response

    async def generate_with_campaign_compat(session, prompt: str) -> str:
        if not hasattr(session, "conversation"):
            return await generate_with_participant_context(session, prompt)
        return await campaign_generate_session_response(session, prompt)

    dnd.generate_session_response = generate_with_campaign_compat
    dnd_router._upupa_dnd_completion_configured = True
