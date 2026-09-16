"""Read-only history of completed DnD campaigns."""
from __future__ import annotations

from datetime import datetime
import re

from aiogram import BaseMiddleware


_PARTY_HISTORY_ALIASES = {"днд партии"}
_TECH_TAG_RE = re.compile(r"\[(?:ACTION|THREAT|NPC|ITEM|REP):[^\]]*\]", re.I)
_MAX_VISIBLE_PARTIES = 20
_PLOT_LIMIT = 64
_SUMMARY_LIMIT = 96


def _normalize_command(text: str | None) -> str:
    value = " ".join(str(text or "").strip().casefold().split())
    return value.rstrip(" ?!.,:;").strip()


def is_party_history_command(text: str | None) -> bool:
    return _normalize_command(text) in _PARTY_HISTORY_ALIASES


def _campaign_module(dnd):
    from AI import dnd_campaign

    dnd_campaign._load_archive(dnd)
    return dnd_campaign


def _compact_text(value, limit: int) -> str:
    text = _TECH_TAG_RE.sub("", str(value or ""))
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip(" ,.;:—-") + "…"


def _completed_date(row: dict) -> str:
    raw = str(row.get("completed_at") or "").strip()
    if not raw:
        return "дата неизвестна"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw.split("T", 1)[0] or "дата неизвестна"
    return parsed.strftime("%d.%m.%Y")


def _campaign_summary(row: dict) -> str:
    for candidate in (
        row.get("epilogue"),
        row.get("finale"),
        (row.get("scenes") or [None])[-1],
    ):
        summary = _compact_text(candidate, _SUMMARY_LIMIT)
        if summary:
            return summary
    return "Итог не сохранился. Видимо, мастер унёс его в могилу."


def render_party_history(dnd, chat_id: int) -> str:
    campaign = _campaign_module(dnd)
    rows = list((campaign._chat_history(chat_id).get("campaigns") or []))
    if not rows:
        return "🎲 Прошедших партий в сохранённом архиве этого чата пока нет."

    visible = rows[-_MAX_VISIBLE_PARTIES:][::-1]
    lines = ["🎲 Прошедшие партии", f"В архиве: {len(rows)}.", ""]
    for index, row in enumerate(visible, start=1):
        plot = _compact_text(row.get("selected_plot"), _PLOT_LIMIT) or "Безымянная катастрофа"
        lines.append(f"{index}. {_completed_date(row)} — {plot}")
        lines.append(f"   ↳ {_campaign_summary(row)}")

    hidden = len(rows) - len(visible)
    if hidden > 0:
        lines.extend(("", f"…ещё {hidden} более старых записей не показано."))
    return "\n".join(lines)


class DndPartyHistoryMiddleware(BaseMiddleware):
    """Intercept ``днд партии`` before the active-game action collector sees it."""

    async def __call__(self, handler, event, data):
        if not is_party_history_command(getattr(event, "text", None)):
            return await handler(event, data)

        chat = getattr(event, "chat", None)
        if chat is None or not hasattr(event, "answer"):
            return await handler(event, data)

        from AI import dnd

        await event.answer(render_party_history(dnd, int(chat.id)))
        return None


def install_dnd_party_history(dnd_router) -> None:
    """Install the history command before participant-action completion middleware."""
    if getattr(dnd_router, "_upupa_dnd_party_history_configured", False):
        return
    middleware = DndPartyHistoryMiddleware()
    dnd_router.message.outer_middleware(middleware)
    dnd_router._upupa_dnd_party_history_middleware = middleware
    dnd_router._upupa_dnd_party_history_configured = True
