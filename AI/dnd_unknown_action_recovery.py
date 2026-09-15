"""Fail-safe recovery when the DnD model emits an unsupported ACTION tag."""
from __future__ import annotations

import logging
import re


_ACTION_RE = re.compile(r"\[ACTION:(.*?)\]", re.I | re.S)
_BASE_ACTION_PREFIXES = ("POLL", "ROLL", "INPUT", "END")


def _unsupported_action(response: str | None) -> str | None:
    """Return the first action command only when the base parser cannot handle it."""
    match = _ACTION_RE.search(str(response or ""))
    if not match:
        return None
    command = match.group(1).strip()
    upper = command.upper()
    if upper.startswith(_BASE_ACTION_PREFIXES):
        return None
    return command or None


def install_dnd_unknown_action_recovery(dnd) -> None:
    """Re-open the action window if an unsupported ACTION leaves a session stuck."""
    if getattr(dnd, "_upupa_dnd_unknown_action_recovery_installed", False):
        return

    original_parse_turn = dnd.parse_and_execute_turn

    async def parse_turn(bot, chat_id, response):
        unsupported = _unsupported_action(response)
        result = await original_parse_turn(bot, chat_id, response)
        if unsupported is None:
            return result

        session = (getattr(dnd, "dnd_sessions", {}) or {}).get(chat_id)
        if session is None or getattr(session, "state", None) != "RESOLVING":
            return result

        logging.warning(
            "DnD unsupported ACTION recovered chat_id=%s action=%r",
            chat_id,
            unsupported[:240],
        )
        await dnd.open_action_window(bot, chat_id)
        return result

    dnd.parse_and_execute_turn = parse_turn
    dnd._upupa_dnd_unknown_action_recovery_installed = True
