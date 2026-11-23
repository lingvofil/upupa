"""HTTP entry points for the Guru Mini App and its loopback playtest server."""
from __future__ import annotations

import asyncio
from functools import lru_cache
import re
from urllib.parse import urlencode

from aiohttp import web

from core.paths import GURU_VISITS_PATH, PROJECT_ROOT
from games.guru import palm_art
from games.guru.miniapp import REPLIES
from games.webapp_auth import WebAppAuthError, validate_telegram_init_data
from infrastructure.persistence.guru_miniapp import GuruVisits, StaleTurn

ASSETS = PROJECT_ROOT / "assets" / "guru_app"
STORE = GuruVisits(GURU_VISITS_PATH)


@lru_cache(maxsize=4)
def _interior_picture(filename):
    return (ASSETS / "art" / filename).read_bytes()


@lru_cache(maxsize=5)
def _live_palm(hand):
    return palm_art.render_study(hand=hand)


async def serve_guru(request):
    asset = request.query.get("asset", "index.html")
    if asset in {"index.html", "app.js", "live.js", "effects.js", "style.css", "salon.woff2"}:
        path = ASSETS / asset
    elif match := re.fullmatch(r"live-palm-(standard|earth|air|fire|water)\.jpg", asset):
        hand = match[1]
        picture = await asyncio.to_thread(_live_palm, None if hand == "standard" else hand)
        return web.Response(body=picture, content_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
    elif re.fullmatch(r"[a-z][a-z-]*\.(?:jpg|webp)", asset):
        path = ASSETS / "art" / asset
    else:
        raise web.HTTPNotFound()
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


async def _prepare(bot, owner, card, url, image):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultPhoto
    result = InlineQueryResultPhoto(id="guru-card", photo_url=image, thumbnail_url=image,
                                    caption=f"🃏 {card['title']}\n{card['symbol']}\n\n«{card['phrase']}»",
                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                        [InlineKeyboardButton(text="А что здесь видишь ты?", url=url)]]))
    return (await bot.save_prepared_inline_message(user_id=owner, result=result, allow_user_chats=True,
                                                  allow_group_chats=True, allow_channel_chats=True,
                                                  allow_bot_chats=False)).id


async def handle(request, owner, store, *, link, public_url="", bot=None, profile=None):
    try:
        body = await request.json()
        if not isinstance(body, dict) or any(isinstance(v, (list, dict)) for v in body.values()):
            raise ValueError("Неверный запрос.")
        op = body.get("op", "home")
        if op == "interior":
            result = await asyncio.to_thread(store.request, owner, {"op": "interior"})
            picture = await asyncio.to_thread(_interior_picture, result["salon"]["picture"])
            return web.Response(body=picture, content_type="image/jpeg", headers={"Cache-Control": "no-store"})
        if op == "prepare":
            token = body.get("token", "")
            result = await asyncio.to_thread(store.request, owner, {"op": "card", "token": token})
            card = dict(result["card"])
            start = f"guru_c_{token}"
            if body.get("option") is not None:
                index = body["option"]
                if type(index) is not int or not 0 <= index < len(card["variants"]):
                    raise ValueError("Прочтение недоступно.")
                card.update(card["variants"][index])
                start += f"_{index}"
            url = f"{link}?startapp={start}" if bot else f"?view=guru&startapp={start}"
            result = {"url": url, "card": card}
            if bot and public_url.startswith("https://") and body.get("native") is True:
                from aiogram.exceptions import TelegramAPIError
                image = public_url + "?" + urlencode({"view": "guru", "asset": f"card-{card['art']}.jpg"})
                try:
                    result["prepared"] = await _prepare(bot, owner, card, url, image)
                except TelegramAPIError:
                    # Older clients and unavailable preview origins still get the selectable share link.
                    pass
        else:
            result = await asyncio.to_thread(store.request, owner, body, profile=profile)
            room = result.get("room")
            if room and room["role"] == "host" and room["phase"] == "waiting" and room["status"] == "active":
                start = f"guru_live_{room['id']}"
                room["invite"] = f"{link}?startapp={start}" if bot else f"?view=guru&startapp={start}"
        if "history" in result:
            result["replies"] = {key: words[0] for key, words in REPLIES.items()}
    except StaleTurn as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except PermissionError:
        return web.json_response({"error": "Этот ход делает другой игрок."}, status=403)
    except (ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response(result, headers={"Cache-Control": "no-store"})


async def guru_api(request, bot):
    from core.settings import API_TOKEN, BLOCKED_USERS, GURU_APP_LINK, GURU_PUBLIC_GAME_URL
    try:
        identity = validate_telegram_init_data(request.headers.get("X-Telegram-Init-Data", ""), API_TOKEN)
    except WebAppAuthError as exc:
        raise web.HTTPUnauthorized(text="Откройте салон через Telegram.") from exc
    if identity.user_id in BLOCKED_USERS:
        raise web.HTTPForbidden()
    return await handle(request, identity.user_id, STORE, bot=bot, link=GURU_APP_LINK, public_url=GURU_PUBLIC_GAME_URL,
                        profile=(identity.display_name, identity.auth_date) if identity.display_name else None)
