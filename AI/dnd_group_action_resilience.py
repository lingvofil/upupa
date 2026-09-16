"""Keep collected DnD group actions until the master response is generated."""
from __future__ import annotations

import logging


def _format_group_actions_for_model(actions: list[dict]) -> str:
    """Render group actions with stable actor IDs for the model only."""
    lines = []
    for item in actions:
        name = item.get("name") or "Игрок"
        action = item.get("action") or ""
        try:
            user_id = int(item.get("user_id"))
        except (TypeError, ValueError):
            lines.append(f"- {name}: {action}")
        else:
            lines.append(f"- {name} (id={user_id}): {action}")
    return "\n".join(lines)


def install_dnd_group_action_resilience(dnd) -> None:
    """Make group-action resolution transactional around model generation.

    The canonical implementation clears ``pending_actions`` before calling the
    model. If every provider is temporarily unavailable, the players therefore
    have to type the same actions again. This wrapper keeps the collected turn
    until generation succeeds and restores it on a provider failure.
    """
    if getattr(dnd, "_upupa_dnd_group_action_resilience_installed", False):
        return

    async def finalize_group_actions(bot, chat_id: int, prompt_message_id: int):
        session = dnd.dnd_sessions.get(chat_id)
        if not session or session.state != "WAITING_ACTION":
            return
        if int(session.action_prompt_message_id or 0) != int(prompt_message_id):
            return

        pending_actions = dict(getattr(session, "pending_actions", {}) or {})
        actions = list(pending_actions.values())
        target_user_ids = list(getattr(session, "action_target_user_ids", []) or [])
        session.action_deadline = None
        if not actions:
            dnd.persist_dnd_sessions()
            return

        # Block duplicate submissions while the model resolves the already
        # collected turn, but do not consume the turn until generation succeeds.
        session.state = "RESOLVING"
        dnd.persist_dnd_sessions()
        actions_text = dnd._format_group_actions(actions)
        actions_prompt_text = _format_group_actions_for_model(actions)
        await bot.send_message(chat_id, f"🎭 Ход партии:\n{actions_text}")

        try:
            response_text = await dnd.generate_session_response(
                session,
                dnd.with_scene_direction(
                    session,
                    (
                        "Игроки заявили действия одновременно:\n"
                        f"{actions_prompt_text}\n"
                        "Свяжи их в одну общую сцену: учти взаимодействие действий и противоречия. "
                        "Если для заявленного действия нужен бросок, не предрешай его исход: опиши только попытку "
                        "и поставь [ACTION:ROLL]. TARGETS этого броска обязан содержать id именно того игрока, "
                        "чьё действие проверяется. До результата броска не объявляй успех или провал этого действия, "
                        "не выдавай и не отнимай из-за него предметы и не фиксируй другие зависящие от броска последствия. "
                        "Действия с очевидным исходом можно разрешить сразу. Продолжай до 100 слов."
                    ),
                ),
            )
        except Exception:
            logging.exception(
                "DnD group action generation failed; preserving turn chat_id=%s",
                chat_id,
            )
            if dnd.dnd_sessions.get(chat_id) is session:
                session.state = "WAITING_ACTION"
                session.action_prompt_message_id = prompt_message_id
                session.pending_actions = pending_actions
                session.action_target_user_ids = target_user_ids
                session.action_deadline = None
                dnd.persist_dnd_sessions()
            await bot.send_message(
                chat_id,
                "Мастер временно недоступен. Ход сохранён — повторно вводить действия не надо; "
                "ведущий может написать «дальше», чтобы повторить попытку.",
            )
            return

        # Generation succeeded: only now consume the collected turn. Parsing may
        # open the next action/poll window and should see a clean previous turn.
        session.action_prompt_message_id = None
        session.pending_actions = {}
        session.action_target_user_ids = []
        dnd.persist_dnd_sessions()
        session._upupa_resolving_group_actions = True
        try:
            await dnd.parse_and_execute_turn(bot, chat_id, response_text)
        except Exception:
            logging.exception("DnD group action continuation failed chat_id=%s", chat_id)
            await bot.send_message(chat_id, "Мастер завис на коллективном безумии. Продолжаем с нового хода.")
            await dnd.open_action_window(bot, chat_id)
        finally:
            try:
                del session._upupa_resolving_group_actions
            except AttributeError:
                pass

    dnd.finalize_group_actions = finalize_group_actions
    dnd._upupa_dnd_group_action_resilience_installed = True


__all__ = ["install_dnd_group_action_resilience"]
