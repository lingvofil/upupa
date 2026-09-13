"""Telegram mentions for DnD actions addressed to specific participants."""

from __future__ import annotations

import html
import logging
import re


_ACTION_TAG_RE = re.compile(r"\[ACTION:(.*?)\]", flags=re.IGNORECASE | re.DOTALL)
_MAX_MENTION_TARGETS = 2


def _unwrap_bot(bot):
    """Bypass the DnD style proxy for the small mechanical mention notification."""
    current = bot
    seen = set()
    while hasattr(current, "_bot") and id(current) not in seen:
        seen.add(id(current))
        nested = getattr(current, "_bot", None)
        if nested is None or nested is current:
            break
        current = nested
    return current


def _mention_html(dnd, session, target_user_ids) -> str:
    mentions = []
    for value in target_user_ids or []:
        user_id = int(value)
        name = html.escape(str(dnd._participant_name(session, user_id)), quote=True)
        mentions.append(f'<a href="tg://user?id={user_id}">{name}</a>')
    return ", ".join(mentions)


async def _notify_targets(bot, dnd, session, target_user_ids, *, singular: str, plural: str) -> None:
    targets = [int(value) for value in (target_user_ids or [])]
    if not targets or len(targets) > _MAX_MENTION_TARGETS:
        return
    mentions = _mention_html(dnd, session, targets)
    if not mentions:
        return
    label = singular if len(targets) == 1 else plural
    try:
        await _unwrap_bot(bot).send_message(
            session.chat_id,
            f"🎯 {mentions} — {label}.",
            parse_mode="HTML",
        )
    except Exception:
        # Mentioning is a notification convenience and must never block the game.
        logging.exception(
            "DnD target mention failed chat_id=%s targets=%s",
            getattr(session, "chat_id", None),
            targets,
        )


def _targets_from_action(dnd, session, command: str) -> list[int]:
    if session is None:
        return []
    return dnd._resolve_targets(session, dnd._parse_targets(command))


def configure_dnd_target_mentions(dnd_module=None) -> None:
    """Tag at most two TARGETS for INPUT, ROLL and POLL without changing action semantics."""
    if dnd_module is None:
        from AI import dnd as dnd_module

    dnd = dnd_module

    # Target mentions are configured after the full DnD generation stack, so this
    # is also a stable startup hook for the per-player agency guard. Isolated
    # mention-test doubles do not necessarily expose the generation pipeline.
    if hasattr(dnd, "generate_session_response"):
        from AI.dnd_player_agency import configure_dnd_player_agency

        configure_dnd_player_agency(dnd)

    if getattr(dnd, "_upupa_dnd_target_mentions_configured", False):
        return

    original_open_action_window = dnd.open_action_window

    async def open_action_window(bot, chat_id: int, target_user_ids=None):
        session = dnd.dnd_sessions.get(chat_id)
        targets = []
        if session is not None:
            targets = dnd._resolve_targets(session, list(target_user_ids or []))
        if targets:
            await _notify_targets(
                bot,
                dnd,
                session,
                targets,
                singular="твой ход",
                plural="ваш ход",
            )
        return await original_open_action_window(
            bot,
            chat_id,
            target_user_ids=target_user_ids,
        )

    dnd.open_action_window = open_action_window

    original_parse_and_execute_turn = dnd.parse_and_execute_turn

    async def parse_and_execute_turn(bot, chat_id: int, text_response: str):
        session = dnd.dnd_sessions.get(chat_id)
        match = _ACTION_TAG_RE.search(str(text_response or ""))
        if session is not None and match:
            command = match.group(1).strip()
            upper = command.upper()
            if upper.startswith("ROLL"):
                targets = _targets_from_action(dnd, session, command)
                if targets:
                    await _notify_targets(
                        bot,
                        dnd,
                        session,
                        targets,
                        singular="твой бросок",
                        plural="ваш бросок",
                    )
            elif upper.startswith("POLL"):
                targets = _targets_from_action(dnd, session, command)
                if targets:
                    await _notify_targets(
                        bot,
                        dnd,
                        session,
                        targets,
                        singular="твой выбор",
                        plural="ваш выбор",
                    )

        return await original_parse_and_execute_turn(bot, chat_id, text_response)

    dnd.parse_and_execute_turn = parse_and_execute_turn
    dnd._upupa_dnd_target_mentions_configured = True
