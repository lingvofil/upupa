"""Admin-only token usage dashboard served by the existing Mini App HTTP server."""

from __future__ import annotations

import asyncio

from aiohttp import web

from core.paths import STATISTICS_DB_PATH
from core.settings import ADMIN_ID, API_TOKEN
import features.statistics as bot_statistics
from games.webapp_auth import WebAppAuthError, validate_telegram_init_data
from infrastructure.persistence.token_dashboard import TokenDashboardDrilldownRepository


_drilldown_repository = TokenDashboardDrilldownRepository(STATISTICS_DB_PATH)


TOKEN_DASHBOARD_PERIODS: dict[str, int | None] = {
    "1": 1,
    "24": 24,
    "168": 24 * 7,
    "720": 24 * 30,
    "all": None,
}


def _authorize_dashboard_request(request: web.Request) -> int:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    try:
        identity = validate_telegram_init_data(init_data, API_TOKEN)
    except WebAppAuthError as exc:
        raise web.HTTPUnauthorized(text="unauthorized") from exc

    if int(identity.user_id) != int(ADMIN_ID):
        raise web.HTTPForbidden(text="forbidden")
    return int(identity.user_id)


async def serve_token_dashboard(_request: web.Request) -> web.FileResponse:
    response = web.FileResponse("tokens.html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def token_dashboard_api(request: web.Request) -> web.Response:
    _authorize_dashboard_request(request)

    period_key = str(request.query.get("period", "24")).strip().lower()
    if period_key not in TOKEN_DASHBOARD_PERIODS:
        raise web.HTTPBadRequest(text="invalid period")

    period_hours = TOKEN_DASHBOARD_PERIODS[period_key]
    user_id_raw = request.query.get("user_id")
    if user_id_raw is not None:
        try:
            user_id = int(user_id_raw)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="invalid user_id") from None
        detail = await asyncio.to_thread(
            _drilldown_repository.get_user_detail,
            user_id,
            period_hours,
            request_limit=100,
        )
        response = web.json_response(
            {
                "period": period_key,
                "user_detail": detail,
            }
        )
    else:
        report = await bot_statistics.get_model_usage_report(
            period_hours,
            limit=20,
        )
        response = web.json_response(
            {
                "period": period_key,
                "report": report,
            }
        )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


__all__ = [
    "TOKEN_DASHBOARD_PERIODS",
    "serve_token_dashboard",
    "token_dashboard_api",
]
