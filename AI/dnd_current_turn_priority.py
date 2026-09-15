"""Keep the unresolved DnD request as the most salient prompt block."""
from __future__ import annotations


CURRENT_REQUEST_MARKER = "ТЕКУЩИЙ ЗАПРОС DND — ЕДИНСТВЕННЫЙ НЕЗАВЕРШЁННЫЙ ХОД"
CURRENT_REQUEST_GUARD = (
    "Все более ранние сообщения игроков в истории уже были обработаны ведущим. "
    "Используй их только как прошлые события и контекст. Не исполняй и не пересказывай "
    "старые действия заново. Разрешай именно запрос, который идёт ниже."
)


def prioritize_current_request(prompt: str, *, campaign_marker: str) -> str:
    """Move appended campaign context before the live request and label that request explicitly."""
    text = str(prompt or "")
    marker = str(campaign_marker or "").strip()
    split_token = f"\n\n{marker}" if marker else ""

    if split_token and split_token in text:
        current_request, context_tail = text.rsplit(split_token, 1)
        campaign_context = marker + context_tail
        return (
            f"{campaign_context}\n\n"
            f"{CURRENT_REQUEST_MARKER}.\n"
            f"{CURRENT_REQUEST_GUARD}\n\n"
            f"{current_request.strip()}"
        )

    return (
        f"{CURRENT_REQUEST_MARKER}.\n"
        f"{CURRENT_REQUEST_GUARD}\n\n"
        f"{text.strip()}"
    )


def install_dnd_current_turn_priority(dnd, *, campaign_marker: str) -> None:
    """Install before the campaign wrapper so campaign state can be reordered behind the live turn."""
    if getattr(dnd, "_upupa_dnd_current_turn_priority_installed", False):
        return

    original_generate = dnd.generate_session_response

    async def prioritized_generate(session, prompt: str) -> str:
        return await original_generate(
            session,
            prioritize_current_request(prompt, campaign_marker=campaign_marker),
        )

    dnd.generate_session_response = prioritized_generate
    dnd._upupa_dnd_current_turn_priority_installed = True


__all__ = [
    "CURRENT_REQUEST_GUARD",
    "CURRENT_REQUEST_MARKER",
    "install_dnd_current_turn_priority",
    "prioritize_current_request",
]
