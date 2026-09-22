"""Make collected DnD group actions durable before provider generation."""
from __future__ import annotations

import logging
import re


_GROUP_ACTION_KIND = "GROUP_ACTION_CONTINUATION"
_GROUP_PROGRESS_CORRECTION_KIND = "GROUP_PROGRESS_CORRECTION"
_ACTION_RE = re.compile(r"\[ACTION:([^;\]]+)([^\]]*)\]", re.I | re.S)
_INSPECTION_RE = re.compile(
    r"\b(осмотр|осматри|огляд|ищ|поиск|смотр|прислуш|изуч|развед|провер|обыск|замет)\w*",
    re.I,
)
_DEFER_RE = re.compile(
    r"\b(осматривай|осматривайтесь|думай|думайте|ищи|ищите|смотри|смотрите|"
    r"решай|решайте|продолжай|продолжайте|можете\s+осмотреть)\w*",
    re.I,
)
_CONCRETE_FEEDBACK_RE = re.compile(
    r"\b(замеч|обнаруж|наход|увид|видн|слыш|услыш|чувству|запах|след|улика|"
    r"двер|проход|надпис|щель|шорох|пусто|стен|предмет|оказыва|выясн|понима)\w*",
    re.I,
)


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


def _ensure_progress_state(session) -> None:
    try:
        session.group_input_streak = max(
            0,
            int(getattr(session, "group_input_streak", 0) or 0),
        )
    except (TypeError, ValueError):
        session.group_input_streak = 0


def _action_kind(response: str) -> tuple[str | None, str]:
    match = _ACTION_RE.search(str(response or ""))
    if not match:
        return None, ""
    return match.group(1).upper(), match.group(2) or ""


def _response_body(response: str) -> str:
    return _ACTION_RE.sub("", str(response or ""), count=1).strip()


def _group_action_block(source_prompt: str) -> str:
    text = str(source_prompt or "")
    marker = "Игроки заявили действия одновременно:\n"
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[1]
    for stop in (
        "\nСначала явно учти",
        "\nСначала учти",
        "\nРЕЖИССЁР СЦЕНЫ:",
        "\n\nДИСЦИПЛИНА",
    ):
        if stop in tail:
            tail = tail.split(stop, 1)[0]
    return tail.strip()[:2400]


def _is_untargeted_group_input(response: str) -> bool:
    action, suffix = _action_kind(response)
    return action == "INPUT" and "TARGETS:" not in suffix.upper()


def _inspection_was_deferred(source_prompt: str, response: str) -> bool:
    actions = _group_action_block(source_prompt)
    if not actions or not _INSPECTION_RE.search(actions):
        return False
    if not _is_untargeted_group_input(response):
        return False
    body = _response_body(response)
    if _CONCRETE_FEEDBACK_RE.search(body):
        return False
    normalized = " ".join(body.split())
    return bool(_DEFER_RE.search(normalized)) or len(normalized) < 90


def _progress_correction_reason(session, pending: dict, response: str) -> str | None:
    source_kind = str(pending.get("source_request_kind") or "").upper()
    if source_kind != _GROUP_ACTION_KIND:
        return None
    source_prompt = str(pending.get("source_prompt") or "")
    if _inspection_was_deferred(source_prompt, response):
        return "inspection-without-feedback"
    _ensure_progress_state(session)
    if (
        int(session.group_input_streak) >= 2
        and _is_untargeted_group_input(response)
    ):
        return "third-consecutive-group-input"
    return None


def _progress_correction_prompt(pending: dict, response: str, reason: str) -> str:
    actions = _group_action_block(str(pending.get("source_prompt") or ""))
    return (
        "СЛУЖЕБНАЯ КОРРЕКЦИЯ ГРУППОВОГО ХОДА. Предыдущий ответ не продвинул игру. "
        "Не повторяй и не комментируй ошибочный ответ. Сначала РАЗРЕШИ каждую исходную заявку. "
        "Для осмотра/поиска: если бросок не нужен — сообщи конкретную новую деталь, улику, отсутствие находок "
        "или другую фактическую обратную связь; если исход реально неопределён и провал имеет цену — дай адресный "
        "ACTION:ROLL соответствующему игроку. Нельзя отвечать только «осматривайтесь», «думайте», «ищите» и снова "
        "возвращать тот же общий ход. Если это уже третий общий INPUT подряд, материально измени ситуацию, цель, "
        "доступную информацию или позицию партии; ещё один общий INPUT допустим только после такого явного продвижения. "
        "Не добавляй случайную катастрофу ради темпа. До 100 слов.\n"
        f"ПРИЧИНА КОРРЕКЦИИ: {reason}\n"
        f"ИСХОДНЫЕ ЗАЯВКИ:\n{actions or 'см. текущий structured state'}\n"
        f"НЕУДАЧНЫЙ ОТВЕТ:\n{_response_body(response)[:1200]}"
    )


def _update_group_input_streak(session, pending: dict, response: str) -> None:
    _ensure_progress_state(session)
    source_kind = str(pending.get("source_request_kind") or "").upper()
    if source_kind not in {_GROUP_ACTION_KIND, _GROUP_PROGRESS_CORRECTION_KIND}:
        return
    if _is_untargeted_group_input(response):
        session.group_input_streak += 1
    else:
        session.group_input_streak = 0


def install_dnd_group_action_resilience(dnd, *, state_policy=None) -> None:
    """Reserve an exact group turn before Telegram/provider side effects."""
    if getattr(dnd, "_upupa_dnd_group_action_resilience_installed", False):
        return

    if state_policy is not None:
        state_policy.add_ensure_hook(_ensure_progress_state)
        state_policy.add_state_field(
            "group_input_streak",
            lambda session: int(getattr(session, "group_input_streak", 0) or 0),
        )
        state_policy.add_restore_hook(
            lambda session, data: setattr(
                session,
                "group_input_streak",
                max(0, int((data or {}).get("group_input_streak", 0) or 0)),
            )
        )

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
                "Сначала явно учти КАЖДУЮ заявку: не пропускай бытовые, исследовательские и социальные действия "
                "только потому, что рядом есть более эффектная угроза. Простое действие вроде еды, осмотра, разговора "
                "или попытки кого-то заткнуть должно получить понятную реакцию мира и не обязано запускать новый экшен. "
                "После этого свяжи совместимые последствия в одну сцену; противоречия между заявками тоже покажи явно. "
                "ОСОБЕННО для осмотра, поиска, прислушивания и изучения: нельзя ответить «да-да, осматривайтесь/думайте» "
                "и снова открыть тот же ход. Если бросок не нужен, дай конкретный результат — что именно заметили, "
                "услышали, не нашли или поняли. Если бросок нужен, назначь его конкретному заявившему игроку."
                + opening_rules
                + " Если для конкретной заявки нужен бросок, не предрешай его исход: опиши только попытку "
                "и поставь [ACTION:ROLL]. TARGETS этого броска обязан содержать id именно того игрока, "
                "чьё действие проверяется. До результата броска не объявляй успех или провал этого действия, "
                "не выдавай и не отнимай из-за него предметы и не фиксируй другие зависящие от броска последствия. "
                "Действия с очевидным исходом можно разрешить сразу. Обычно 40–60 слов, максимум 70."
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

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        pending = (
            dict(getattr(session, "pending_generated_result", {}) or {})
            if session is not None
            else {}
        )
        if session is not None:
            reason = _progress_correction_reason(session, pending, response)
            if reason:
                from AI.dnd_result_recovery import (
                    continue_pending_generation,
                    transition_to_generation_request,
                )

                correction_prompt = _progress_correction_prompt(
                    pending,
                    response,
                    reason,
                )
                logging.warning(
                    "DnD correcting stalled group resolution chat_id=%s reason=%s streak=%s",
                    chat_id,
                    reason,
                    getattr(session, "group_input_streak", 0),
                )
                if transition_to_generation_request(
                    session,
                    correction_prompt,
                    kind=_GROUP_PROGRESS_CORRECTION_KIND,
                ):
                    dnd.persist_dnd_sessions()
                    completed = await continue_pending_generation(dnd, bot, session)
                    if not completed:
                        raise RuntimeError(
                            "DnD group progress correction generation failed"
                        )
                    return None

            _update_group_input_streak(session, pending, response)
            dnd.persist_dnd_sessions()

        return await original_parse(bot, chat_id, response)

    dnd.parse_and_execute_turn = parse_turn
    dnd._upupa_dnd_group_action_resilience_installed = True


__all__ = [
    "_group_action_block",
    "_inspection_was_deferred",
    "_progress_correction_reason",
    "install_dnd_group_action_resilience",
]
