"""Preserve player agency around fixed-option DnD polls."""
from __future__ import annotations

import logging
import re
import time


POLL_AGENCY_MARKER = "ГОЛОСОВАНИЯ DND: ЗАКРЫТЫЙ ВЫБОР НЕ ДОЛЖЕН ЗАПИРАТЬ ИГРОКОВ"
CUSTOM_POLL_OPTION = "🗣 Свой вариант"

POLL_AGENCY_RULES = f"""
{POLL_AGENCY_MARKER}.
Используй ACTION:POLL для настоящей ОБЩЕЙ сюжетной развилки с несколькими понятными путями.
Для обычного личного действия используй ACTION:INPUT. Любой конкретный путь, который ты называешь в художественном
тексте как доступную возможность (например «попробовать договориться», «уйти», «осмотреть рынок»), обязан присутствовать
среди OPTIONS этого же POLL. Не упоминай доступный путь в тексте и одновременно не прячь его из кнопок.
Для общей развилки предложи 2–3 конкретных пути даже если возможна импровизация.
Runtime добавляет «Свой вариант». За сюжет нужна хотя бы одна такая развилка;
после 4–6 решений без голосования подготовь следующую, сохраняя причинность.
""".strip()

_POLL_RE = re.compile(r"\[ACTION:POLL(?P<body>[^\]]*)\]", re.I | re.S)
_TARGETS_RE = re.compile(r"(?:^|;)TARGETS:([0-9,\s]+)(?=;|$)", re.I)
_OPTIONS_RE = re.compile(r"(?:^|;)OPTIONS:(.*)$", re.I | re.S)


def _targets(body: str) -> list[int]:
    match = _TARGETS_RE.search(str(body or "").strip(";"))
    if not match:
        return []
    result = []
    for token in match.group(1).split(","):
        token = token.strip()
        if token.isdigit() and int(token) not in result:
            result.append(int(token))
    return result


def _is_custom_option(value: str) -> bool:
    normalized = " ".join(str(value or "").casefold().replace("ё", "е").split())
    return normalized in {
        "свой вариант",
        "🗣 свой вариант",
        "другое",
        "другой вариант",
        "предложить свой вариант",
    }


def ensure_group_poll_free_choice(response: str) -> str:
    """Append one free-choice escape hatch to participant group polls."""
    text = str(response or "")
    match = _POLL_RE.search(text)
    if not match:
        return text

    body = match.group("body") or ""
    if len(_targets(body)) == 1:
        return text

    options_match = _OPTIONS_RE.search(body)
    if not options_match:
        return text

    authored = [
        item.strip()
        for item in options_match.group(1).split(";")
        if item.strip() and not _is_custom_option(item)
    ]
    if not authored:
        return text

    options = authored[:3] + [CUSTOM_POLL_OPTION]
    prefix = body[: options_match.start()].rstrip(";")
    if prefix and not prefix.startswith(";"):
        prefix = ";" + prefix
    replacement = f"[ACTION:POLL{prefix};OPTIONS:{';'.join(options)}]"
    return text[: match.start()] + replacement + text[match.end() :]


def _custom_wins(dnd, session, options: list[str]) -> bool:
    try:
        custom_index = options.index(CUSTOM_POLL_OPTION)
    except ValueError:
        return False
    counts = list(dnd._eligible_poll_vote_counts(session, options))
    if custom_index >= len(counts):
        return False
    custom_votes = int(counts[custom_index] or 0)
    return custom_votes > 0 and custom_votes == max(counts, default=0)


async def _resolve_custom_poll(dnd, bot, session, chat_id: int, message_id: int, options: list[str]):
    poll_id = str(getattr(session, "current_poll_id", "") or "")
    if not poll_id:
        return False

    finalizing = getattr(dnd, "_finalizing_polls", None)
    if isinstance(finalizing, set):
        if poll_id in finalizing:
            return True
        finalizing.add(poll_id)

    try:
        poll_snapshot = dict(getattr(session, "pending_poll", None) or {})
        targets = list(poll_snapshot.get("target_user_ids") or [])
        outcome = f"Выбор сделан: {CUSTOM_POLL_OPTION}"
        session.last_resolved_poll = {
            "poll_id": poll_id,
            "scene_text": str(poll_snapshot.get("scene_text") or "").strip(),
            "options": list(options),
            "outcome": outcome,
            "resolved_at": time.time(),
        }
        dnd.poll_map.pop(poll_id, None)
        session.current_poll_id = None
        session.pending_poll = None
        session.state = "WAITING_ACTION"
        session.action_prompt_message_id = None
        session.pending_actions = {}
        session.action_deadline = None
        session.action_target_user_ids = list(targets)
        dnd.persist_dnd_sessions()

        try:
            await bot.stop_poll(chat_id, int(message_id))
        except Exception:
            logging.info(
                "DnD custom poll stop failed chat_id=%s poll_id=%s",
                chat_id,
                poll_id,
                exc_info=True,
            )

        await bot.send_message(
            chat_id,
            "✅ Свой вариант. Теперь заявляйте, что именно делаете.",
        )
        await dnd.open_action_window(
            bot,
            chat_id,
            target_user_ids=targets,
        )
        return True
    finally:
        if isinstance(finalizing, set):
            finalizing.discard(poll_id)


def install_dnd_poll_agency(dnd) -> None:
    if getattr(dnd, "_upupa_dnd_poll_agency_installed", False):
        return

    if POLL_AGENCY_MARKER not in dnd.DND_SYSTEM_PROMPT:
        dnd.DND_SYSTEM_PROMPT = dnd.DND_SYSTEM_PROMPT.rstrip() + "\n\n" + POLL_AGENCY_RULES

    original_parse = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        session = dnd.dnd_sessions.get(chat_id)
        if session and dnd._is_participant_mode(session):
            response = ensure_group_poll_free_choice(response)
        return await original_parse(bot, chat_id, response)

    dnd.parse_and_execute_turn = parse_turn

    original_finalize = dnd.finalize_poll

    async def finalize_poll(bot, chat_id: int, message_id: int, options: list):
        session = dnd.dnd_sessions.get(chat_id)
        visible_options = list(options or [])
        if (
            session
            and dnd._is_participant_mode(session)
            and CUSTOM_POLL_OPTION in visible_options
        ):
            if _custom_wins(dnd, session, visible_options):
                return await _resolve_custom_poll(
                    dnd,
                    bot,
                    session,
                    chat_id,
                    message_id,
                    visible_options,
                )
            authored_options = [
                option for option in visible_options if option != CUSTOM_POLL_OPTION
            ]
            return await original_finalize(
                bot,
                chat_id,
                message_id,
                authored_options,
            )
        return await original_finalize(bot, chat_id, message_id, visible_options)

    dnd.finalize_poll = finalize_poll
    dnd._upupa_dnd_poll_agency_installed = True


__all__ = [
    "POLL_AGENCY_MARKER",
    "POLL_AGENCY_RULES",
    "CUSTOM_POLL_OPTION",
    "ensure_group_poll_free_choice",
    "install_dnd_poll_agency",
]
