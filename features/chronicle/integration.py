"""Bridge Chronicle capture into the already-installed social accounting middleware."""

from __future__ import annotations

import logging

from features.chronicle.runtime import capture_message as capture_chronicle_message
from features.social_graph.service import SocialInteractionMiddleware as BaseSocialInteractionMiddleware


class ChronicleSocialInteractionMiddleware(BaseSocialInteractionMiddleware):
    """Capture social edges first, Chronicle candidate second, then normal handlers."""

    async def __call__(self, handler, event, data):
        async def after_social(next_event, next_data):
            try:
                await capture_chronicle_message(next_event)
            except Exception as exc:
                logging.error("[chronicle] message capture failed: %s", exc, exc_info=True)
            return await handler(next_event, next_data)

        return await super().__call__(after_social, event, data)
