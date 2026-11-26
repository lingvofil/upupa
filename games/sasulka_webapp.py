"""Serve Sasulka from the bot's existing HTTPS game endpoint."""

import asyncio
from html import escape
import json
import re

from aiohttp import web

from core.paths import PROJECT_ROOT, SASULKA_RANKING_PATH
from games.sasulka_replay import DIFFICULTIES, replay
from games.webapp_auth import WebAppAuthError, validate_telegram_init_data
from infrastructure.persistence.sasulka_ranking import SasulkaRanking


ASSETS = PROJECT_ROOT / "assets" / "sasulka_app"
STATIC_FILES = {"drop.css", "drop.js", "drop-engine.js", "drop-scene.js", "drop-ranking.js", "village.webp"}
CONTENT_TYPES = {".css": "text/css", ".js": "text/javascript", ".webp": "image/webp"}
STORE = SasulkaRanking(SASULKA_RANKING_PATH)
USERNAME = re.compile(r"[a-zA-Z0-9_]{1,32}")
MAX_REQUEST_BYTES = 90000


async def serve_sasulka(request):
    from core.settings import SASULKA_APP_LINK

    asset = request.query.get("asset", "drop.html")
    headers = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}
    if asset == "drop.html":
        page = await asyncio.to_thread((ASSETS / asset).read_text, encoding="utf-8")
        return web.Response(text=page.replace("{{app_link}}", escape(SASULKA_APP_LINK)),
                            content_type="text/html", headers=headers)
    if asset not in STATIC_FILES:
        raise web.HTTPNotFound()
    path = ASSETS / asset
    return web.FileResponse(path, headers={**headers, "Content-Type": CONTENT_TYPES[path.suffix]})


def _json(data, status=200):
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store"})


async def sasulka_api(request):
    from core.settings import API_TOKEN, BLOCKED_USERS

    init_data = request.headers.get("X-Telegram-Init-Data", "")
    try:
        identity = validate_telegram_init_data(init_data, API_TOKEN)
    except WebAppAuthError:
        return _json({"error": "Открой игру заново через Telegram."}, status=401)
    if identity.user_id in BLOCKED_USERS:
        return _json({"error": "Рейтинг недоступен."}, status=403)

    username = identity.username if USERNAME.fullmatch(identity.username) else ""
    raw = bytearray()
    async for chunk in request.content.iter_chunked(8192):
        raw.extend(chunk)
        if len(raw) > MAX_REQUEST_BYTES:
            return _json({"error": "Слишком большая запись игры."}, status=413)
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        body = None
    if not isinstance(body, dict):
        return _json({"error": "Хреновая запись игры."}, status=400)

    if body.get("op") == "ranking":
        sort = body.get("sort")
        if sort not in ("points", "record", "solved"):
            sort = "points"
        rows = await asyncio.to_thread(STORE.ranking, identity, username, sort)
        return _json({"rows": rows, "name": f"@{username}" if username else ""})

    if body.get("op") != "result":
        return _json({"error": "Неизвестная операция."}, status=400)
    if not username:
        return _json({"error": "Укажи @username в профиле Telegram и открой игру заново."}, status=422)

    level, difficulty, seed = (body.get(key) for key in ("id", "difficulty", "seed"))
    length = await asyncio.to_thread(replay, level, difficulty, seed, body.get("trace"))
    if length is None:
        return _json({"error": "Запись не подтверждает результат."}, status=400)
    gained = await asyncio.to_thread(STORE.submit, identity, username, level, seed, length,
                                     DIFFICULTIES[difficulty][-1])
    return _json({"gained": gained})
