"""Aiogram middleware for Chronicle reaction updates."""

from __future__ import annotations

import logging

from aiogram import BaseMiddleware

from features.chronicle.service import capture_reaction, capture_reaction_count


class ChronicleReactionMiddleware(BaseMiddleware):
    """Observe reactions without consuming the existing social-graph handler."""

    async def __call__(self, handler, event, data):
        try:
            if hasattr(event, "new_reaction"):
                await capture_reaction(event)
            elif hasattr(event, "reactions"):
                await capture_reaction_count(event)
        except Exception as exc:
            logging.error("[chronicle] reaction capture failed: %s", exc, exc_info=True)
        return await handler(event, data)
