"""Make collected DnD group actions durable before provider generation."""
from __future__ import annotations


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
            lines.append(f"- {name}: {action} (id={user_id})")
    return "\n".join(lines)


def install_dnd_group_action_resilience(dnd) -> None:
    """Reserve an exact group turn before Telegram/provider side effects."""
    if getattr(dnd, "_upupa_dnd_group_action_resilience_installed", False):
        return

    async def finalize_group_actions(bot, chat_id: int, prompt_message_id: int):
        session = dnd.dnd_sessions.get(chat_id)
        if not session or session.state != "WAITING_ACTION":
            return
        if int(session.action_prompt_message_id or 0) != int(prompt_message_id):
            return

        actions = list((getattr(session, "pending_actions", {}) or {}).values())
        session.action_deadline = None
        if not actions:
            dnd.persist_dnd_sessions()
            return

        actions_text = dnd._format_group_actions(actions)
        actions_prompt_text = _format_group_actions_for_model(actions)
        continuation_prompt = dnd.with_scene_direction(
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
        )

        from AI.dnd_result_recovery import (
            continue_pending_generation,
            reserve_generation_request,
        )

        if not reserve_generation_request(
            session,
            continuation_prompt,
            kind="GROUP_ACTION_CONTINUATION",
            effects=[
                {
                    "method": "send_message",
                    "chat_id": chat_id,
                    "text": f"🎭 Ход партии:\n{actions_text}",
                }
            ],
        ):
            await bot.send_message(
                chat_id,
                "Этот ход уже восстанавливается. Ведущий может написать «дальше».",
            )
            return

        # Once the exact request is durable, the mutable collection window is no
        # longer the source of truth and can be consumed atomically.
        session.state = "RESOLVING"
        session.action_prompt_message_id = None
        session.pending_actions = {}
        session.action_deadline = None
        session.action_target_user_ids = []
        dnd.persist_dnd_sessions()

        completed = await continue_pending_generation(dnd, bot, session)
        if not completed and dnd.dnd_sessions.get(chat_id) is session:
            await bot.send_message(
                chat_id,
                "Мастер временно недоступен, но коллективный ход сохранён. "
                "Ведущий может написать «дальше» — повторно вводить действия не надо.",
            )

    dnd.finalize_group_actions = finalize_group_actions
    dnd._upupa_dnd_group_action_resilience_installed = True


__all__ = ["install_dnd_group_action_resilience"]
