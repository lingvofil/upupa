"""Make collected DnD group actions durable before provider generation."""
from __future__ import annotations


_GROUP_ACTION_KIND = "GROUP_ACTION_CONTINUATION"


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


def _resolution_budget(action_count: int) -> tuple[int, int]:
    """Give simultaneous actions enough narrative room without flooding chat."""
    count = max(1, int(action_count or 1))
    preferred = min(280, 100 + 40 * count)
    maximum = preferred + 80
    return preferred, maximum


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
        personal = len(getattr(session, "action_target_user_ids", []) or []) == 1
        heading = "🎭 Личный ход:" if personal else "🎭 Ход партии:"
        actions_prompt_text = _format_group_actions_for_model(actions)
        preferred_words, max_words = _resolution_budget(len(actions))
        opening_round = int(getattr(session, "scene_count", 0) or 0) <= 1
        opening_rules = (
            " ЭТО ПЕРВЫЙ ОБЩИЙ КРУГ: сначала дай каждому герою короткое конкретное последствие его заявки "
            "или новую деталь, которую он заметил/получил в разговоре. Не сжимай четыре разных действия в один "
            "немедленный драматический поворот. Потихоньку сведи их последствия в общую канву. Не вводи новую "
            "атаку, погоню, катастрофу, срочный дедлайн или обязательное голосование, если сами заявки игроков "
            "прямо этого не вызвали. После спокойного разрешения допустим ещё один общий ACTION:INPUT."
            if opening_round
            else ""
        )
        continuation_prompt = dnd.with_scene_direction(
            session,
            (
                "Игроки заявили действия одновременно:\n"
                f"{actions_prompt_text}\n"
                "Сначала РАЗРЕШИ КАЖДУЮ заявку, а не просто перескажи её: для каждого участника должна быть видна "
                "прямая причинно-следственная связь «что сделал -> что из этого вышло». Не пропускай бытовые, "
                "исследовательские и социальные действия только потому, что рядом есть более эффектная угроза. "
                "Простое действие вроде еды, осмотра, разговора или попытки кого-то заткнуть должно получить "
                "конкретную реакцию мира и не обязано запускать новый экшен. Даже если действие не двигает основной "
                "сюжет, покажи его локальный эффект. После этого свяжи совместимые последствия в одну сцену; "
                "противоречия между заявками тоже покажи явно. "
                "ОСОБЕННО для осмотра, поиска, прислушивания и изучения: нельзя ответить «да-да, осматривайтесь/думайте» "
                "и снова открыть тот же ход. Если бросок не нужен, дай конкретный результат — что именно заметили, "
                "услышали, не нашли или поняли. Если бросок нужен, назначь его конкретному заявившему игроку."
                + opening_rules
                + " Если для конкретной заявки нужен бросок, не предрешай его исход: опиши только попытку "
                "и поставь [ACTION:ROLL]. TARGETS этого броска обязан содержать id именно того игрока, "
                "чьё действие проверяется. До результата броска не объявляй успех или провал этого действия, "
                "не выдавай и не отнимай из-за него предметы и не фиксируй другие зависящие от броска последствия. "
                "Действия с очевидным исходом можно разрешить сразу. Не заканчивай ответ одним перечислением заявок "
                "и новым «ходом партии»: перед следующим INPUT в сцене должно появиться хотя бы одно наблюдаемое "
                "изменение, новая информация, реакция мира или честно зафиксированное отсутствие результата. "
                f"Ориентир для этого коллективного хода — около {preferred_words} слов, максимум {max_words}."
            ),
        )

        from AI.dnd_result_recovery import (
            continue_pending_generation,
            reserve_generation_request,
        )

        if not reserve_generation_request(
            session,
            continuation_prompt,
            kind=_GROUP_ACTION_KIND,
            effects=[
                {
                    "method": "send_message",
                    "chat_id": chat_id,
                    "text": f"{heading}\n{actions_text}",
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

        session._upupa_resolving_group_actions = True
        try:
            completed = await continue_pending_generation(dnd, bot, session)
        finally:
            try:
                del session._upupa_resolving_group_actions
            except AttributeError:
                pass

        if not completed and dnd.dnd_sessions.get(chat_id) is session:
            await bot.send_message(
                chat_id,
                "Мастер временно недоступен, но коллективный ход сохранён. "
                "Ведущий может написать «дальше» — повторно вводить действия не надо.",
            )

    dnd.finalize_group_actions = finalize_group_actions
    dnd._upupa_dnd_group_action_resilience_installed = True


__all__ = [
    "_GROUP_ACTION_KIND",
    "_resolution_budget",
    "install_dnd_group_action_resilience",
]
