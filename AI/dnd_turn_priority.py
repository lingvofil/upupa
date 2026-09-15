"""Keep the latest participant actions dominant in DnD model prompts."""
from __future__ import annotations

import logging


_GROUP_ACTIONS_MARKER = "Игроки заявили действия одновременно:"
_GROUP_ACTIONS_END = "Разреши их в одной общей сцене"

TURN_PRIORITY_GUARD = (
    "КРИТИЧЕСКИ ВАЖНО — ЭТО ТЕКУЩИЙ ХОД ПАРТИИ. "
    "Сначала разреши КАЖДОЕ заявленное ниже действие именно в этом ответе. "
    "Если действие пассивное, бессмысленное или невозможно, всё равно явно отрази его в сцене и покажи непосредственное последствие. "
    "Не продолжай вместо этого действия из прошлого хода и не подменяй текущий ход старой сюжетной веткой. "
    "Только после разрешения этих действий можно двигать текущую сцену дальше."
)


def _extract_group_actions(prompt: str) -> str | None:
    text = str(prompt or "")
    start = text.find(_GROUP_ACTIONS_MARKER)
    if start < 0:
        return None
    start += len(_GROUP_ACTIONS_MARKER)
    tail = text[start:]
    end = tail.find(_GROUP_ACTIONS_END)
    if end >= 0:
        tail = tail[:end]
    lines = [line.rstrip() for line in tail.splitlines() if line.lstrip().startswith("-")]
    actions = "\n".join(lines).strip()
    return actions or None


def configure_dnd_turn_priority(dnd) -> None:
    """Re-append the current group turn after all campaign/state context."""
    if getattr(dnd, "_upupa_dnd_turn_priority_configured", False):
        return

    original_generate = dnd.generate_session_response

    async def generate(session, prompt: str) -> str:
        actions = _extract_group_actions(prompt)
        if not actions:
            return await original_generate(session, prompt)

        reinforced = (
            str(prompt).rstrip()
            + "\n\n"
            + TURN_PRIORITY_GUARD
            + "\nТЕКУЩИЕ ДЕЙСТВИЯ:\n"
            + actions
        )
        logging.info(
            "DnD turn priority applied chat_id=%s actions=%s",
            getattr(session, "chat_id", None),
            len(actions.splitlines()),
        )
        return await original_generate(session, reinforced)

    dnd.generate_session_response = generate
    dnd._upupa_dnd_turn_priority_configured = True


__all__ = ["TURN_PRIORITY_GUARD", "configure_dnd_turn_priority"]
