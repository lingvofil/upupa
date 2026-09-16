"""Turn-control helpers for participant DnD.

Provides two related safeguards:
- a host/admin can skip an addressed turn when the targeted player is absent;
- after individual spotlight scenes, the model is nudged back toward a shared
  party INPUT a little more often without allowing consecutive empty group turns.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message


GROUP_TURN_RULES = (
    "БАЛАНС ОБЩИХ ХОДОВ: в режиме с участниками общий свободный ход партии [ACTION:INPUT] без TARGETS "
    "должен встречаться немного чаще — ориентир примерно каждый второй подходящий интерактивный эпизод. "
    "После одного индивидуального хода уже повышай приоритет общего INPUT; после двух индивидуальных ходов "
    "подряд, если нет обязательного немедленного последствия или броска, следующий интерактивный ход делай общим. "
    "Не ставь два общих INPUT подряд и не ломай причинность только ради этой частоты."
)


def _group_turn_context(session) -> str:
    try:
        streak = max(0, int(getattr(session, "spotlight_individual_streak", 0) or 0))
    except (TypeError, ValueError):
        streak = 0
    if streak >= 2:
        return (
            "\nСЕЙЧАС: уже было два индивидуальных хода подряд. Если сцена не требует немедленной адресной реакции, "
            "верни инициативу всей партии через общий [ACTION:INPUT] без TARGETS."
        )
    if streak == 1:
        return (
            "\nСЕЙЧАС: только что был индивидуальный ход. При следующем естественном выборе слегка предпочти "
            "общий [ACTION:INPUT] без TARGETS, если это не мешает причинности."
        )
    return ""


def _target_names(dnd, session, target_user_ids: list[int]) -> list[str]:
    names = []
    for user_id in target_user_ids:
        try:
            name = dnd._participant_name(session, int(user_id))
        except Exception:
            name = f"ID {int(user_id)}"
        names.append(str(name))
    return names


def _advance_past_skipped_targets(session, target_user_ids: list[int]) -> None:
    """Move the spotlight cursor past skipped addressed players without counting a turn."""
    try:
        from AI.dnd_spotlight import next_spotlight

        expected = next_spotlight(session)
    except Exception:
        logging.exception("DnD could not inspect spotlight while skipping a turn")
        return

    skipped = {int(value) for value in target_user_ids}
    if expected is None or int(expected) not in skipped:
        return

    order = [int(value) for value in (getattr(session, "spotlight_order", None) or [])]
    if not order or int(expected) not in order:
        return

    cursor = (order.index(int(expected)) + 1) % len(order)
    for _ in range(len(order) - 1):
        if order[cursor] not in skipped:
            break
        cursor = (cursor + 1) % len(order)
    session.spotlight_cursor = cursor


def _skippable_state(session) -> tuple[str, list[int]] | None:
    state = str(getattr(session, "state", "") or "")
    if state == "WAITING_ACTION":
        if getattr(session, "pending_actions", None):
            return None
        targets = [int(value) for value in (getattr(session, "action_target_user_ids", None) or [])]
        return ("ход", targets) if targets else None

    if state == "WAITING_ROLL":
        roll = getattr(session, "pending_roll", None) or {}
        targets = [int(value) for value in (roll.get("target_user_ids") or [])]
        return ("бросок", targets) if targets else None

    if state == "WAITING_POLL":
        poll = getattr(session, "pending_poll", None) or {}
        targets = [int(value) for value in (poll.get("target_user_ids") or [])]
        votes = poll.get("votes") or {}
        if targets and not votes:
            return "выбор", targets

    return None


async def skip_absent_turn(dnd, bot, chat_id: int, requester_user_id: int) -> bool:
    """Skip an unanswered addressed action/roll/poll. Return whether it was consumed."""
    session = dnd.dnd_sessions.get(chat_id)
    if not session or not dnd._user_is_host(session, int(requester_user_id)):
        return False

    skippable = _skippable_state(session)
    if not skippable:
        return False
    kind, targets = skippable
    names = _target_names(dnd, session, targets)

    # Close an unanswered addressed poll before dropping its runtime mapping.
    if str(getattr(session, "state", "")) == "WAITING_POLL":
        poll = getattr(session, "pending_poll", None) or {}
        poll_id = str(getattr(session, "current_poll_id", "") or poll.get("poll_id") or "")
        try:
            await bot.stop_poll(
                chat_id=int(poll.get("poll_chat_id") or chat_id),
                message_id=int(poll["message_id"]),
            )
        except Exception:
            logging.exception("DnD stop skipped poll failed chat_id=%s poll_id=%s", chat_id, poll_id)
        if poll_id:
            dnd.poll_map.pop(poll_id, None)

    _advance_past_skipped_targets(session, targets)
    session.state = "RESOLVING"
    session.pending_roll = None
    session.current_poll_id = None
    session.pending_poll = None
    session.action_prompt_message_id = None
    session.pending_actions = {}
    session.action_deadline = None
    session.action_target_user_ids = []
    dnd.persist_dnd_sessions()

    who = ", ".join(names) if names else ", ".join(f"ID {value}" for value in targets)
    await bot.send_message(chat_id, f"⏭️ {kind.capitalize()} {who} пропущен ведущим: игрока сейчас нет.")

    prompt = (
        f"Ведущий пропустил текущий адресный {kind}, потому что игрок недоступен. "
        f"Пропущены персонажи: {who} (ID: {', '.join(map(str, targets))}). "
        "Не придумывай и не исполняй действие за отсутствующего игрока, не бросай за него кубик, "
        "не считай пропуск успехом или провалом и не наказывай персонажа за отсутствие. "
        "Коротко продолжи сцену с текущего состояния и передай инициативу дальше. "
        "По возможности следующий интерактивный момент сделай общим свободным ходом партии [ACTION:INPUT] без TARGETS; "
        "если это нелогично, адресуй его другому доступному герою. Не возвращай пропущенный ход сразу."
    )
    try:
        response_text = await dnd.generate_session_response(
            session,
            dnd.with_scene_direction(session, prompt),
        )
        await dnd.parse_and_execute_turn(bot, chat_id, response_text)
    except Exception:
        logging.exception("DnD continuation after skipped turn failed chat_id=%s", chat_id)
        await bot.send_message(chat_id, "Мастер завис после пропуска. Отдаю ход всей партии.")
        await dnd.open_action_window(bot, chat_id)
    return True


class DndTurnControlMiddleware(BaseMiddleware):
    def __init__(self, dnd) -> None:
        self._dnd = dnd

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if (
            isinstance(event, Message)
            and event.from_user is not None
            and event.text
            and event.text.strip().casefold() == "дальше"
        ):
            consumed = await skip_absent_turn(
                self._dnd,
                event.bot,
                int(event.chat.id),
                int(event.from_user.id),
            )
            if consumed:
                return None
        return await handler(event, data)


def install_dnd_turn_control(dnd, router) -> None:
    """Install group-turn guidance and the host/admin skip middleware once."""
    if getattr(router, "_upupa_dnd_turn_control_installed", False):
        return

    from AI import dnd_campaign as campaign

    if GROUP_TURN_RULES not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + GROUP_TURN_RULES

    original_context = campaign._campaign_context

    def campaign_context(dnd_module, session):
        return original_context(dnd_module, session) + "\n" + GROUP_TURN_RULES + _group_turn_context(session)

    campaign._campaign_context = campaign_context
    router.message.outer_middleware(DndTurnControlMiddleware(dnd))
    router._upupa_dnd_turn_control_installed = True


__all__ = [
    "DndTurnControlMiddleware",
    "GROUP_TURN_RULES",
    "install_dnd_turn_control",
    "skip_absent_turn",
]
