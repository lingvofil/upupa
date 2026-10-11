"""Interactive social-graph Mini App served by the existing Mini App HTTP server."""

from __future__ import annotations

import logging
import time

from aiohttp import web

from core.settings import API_TOKEN
from features.social_graph.service import is_social_graph_enabled
from features.social_graph.webapp import ALLOWED_PERIODS, build_social_payload, chat_from_start_param
from games.webapp_auth import WebAppAuthError, validate_telegram_init_data


MEMBERSHIP_TTL_SECONDS = 600
_ACTIVE_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}
_membership_cache: dict[tuple[int, int], tuple[float, bool]] = {}


def _security_headers(response: web.StreamResponse) -> web.StreamResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def _is_member(bot, chat_id: int, user_id: int) -> bool:
    key = (int(chat_id), int(user_id))
    cached = _membership_cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < MEMBERSHIP_TTL_SECONDS:
        return cached[1]
    try:
        member = await bot.get_chat_member(int(chat_id), int(user_id))
        status = getattr(getattr(member, "status", None), "value", getattr(member, "status", None))
        allowed = str(status) in _ACTIVE_MEMBER_STATUSES
        if str(status) == "restricted":
            allowed = bool(getattr(member, "is_member", True))
    except Exception as exc:
        logging.info("[social_webapp] membership check failed chat=%s user=%s: %s", chat_id, user_id, exc)
        allowed = False
    _membership_cache[key] = (now, allowed)
    return allowed


async def authorize_social_request(request: web.Request, bot) -> tuple[int, int]:
    """Return (chat_id, user_id) for a verified chat member, or raise an HTTP error."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    try:
        identity = validate_telegram_init_data(init_data, API_TOKEN)
    except WebAppAuthError as exc:
        raise web.HTTPUnauthorized(text="unauthorized") from exc

    chat_id = chat_from_start_param(identity.start_param)
    if chat_id is None:
        raise web.HTTPBadRequest(text="not a social graph link")
    if not is_social_graph_enabled(chat_id):
        raise web.HTTPForbidden(text="social graph disabled")
    if not await _is_member(bot, chat_id, identity.user_id):
        raise web.HTTPForbidden(text="not a chat member")
    return chat_id, int(identity.user_id)


async def serve_social_app(_request: web.Request) -> web.FileResponse:
    return _security_headers(web.FileResponse("social.html"))


async def social_api(request: web.Request, bot) -> web.Response:
    chat_id, user_id = await authorize_social_request(request, bot)
    try:
        period = int(request.query.get("period", "30"))
    except ValueError:
        raise web.HTTPBadRequest(text="invalid period") from None
    if period not in ALLOWED_PERIODS:
        raise web.HTTPBadRequest(text="invalid period")
    payload = await build_social_payload(chat_id, period_days=period, viewer_id=user_id)
    return _security_headers(web.json_response(payload))


__all__ = ["authorize_social_request", "serve_social_app", "social_api"]
