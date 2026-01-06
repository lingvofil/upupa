# crocodile.py
import base64
import logging
import random
import time
from aiohttp import web
import socketio

from aiogram import types
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
    InputMediaPhoto,
)

from config import bot

# ================== НАСТРОЙКИ ==================
BOT_USERNAME = "expertyebaniebot"
WEB_APP_SHORT_NAME = "upupadile"

# Если ты используешь Cloudflare Tunnel до localhost:8080 — можно оставить 127.0.0.1
# Если хочешь слушать снаружи напрямую — ставь "0.0.0.0"
SOCKET_SERVER_HOST = "127.0.0.1"
SOCKET_SERVER_PORT = 8080

PREVIEW_UPDATE_INTERVAL = 2.5  # сек

BLANK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)

GAME_WORDS = ["кот", "дом", "лес", "кит", "сыр", "сок", "мяч", "жук", "зуб", "нос"]

# chat_id(str) -> session dict
game_sessions: dict[str, dict] = {}

sio = socketio.AsyncServer(
    async_mode="aiohttp",
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=10 * 1024 * 1024,
)

app = web.Application(client_max_size=20 * 1024 * 1024)
sio.attach(app)


def get_chat_id_from_room(room: str) -> str:
    """
    room = tg start_param
    пример: m4611982229 -> -4611982229
    """
    room = str(room or "")
    if room.startswith("m"):
        return str(int(room.replace("m", "-")))
    return room


async def _ensure_session(chat_id: str) -> dict | None:
    """Если сессии нет — попробуем создать превью-сообщение и восстановить."""
    session = game_sessions.get(chat_id)
    if session:
        return session

    try:
        blank = base64.b64decode(BLANK_PNG_B64)
        new_msg = await bot.send_photo(
            int(chat_id),
            BufferedInputFile(blank, "b.png"),
            caption="🔄 Reload",
        )
        session = {
            "word": "???",
            "drawer_id": 0,
            "drawer_name": "Player",
            "preview_message_id": new_msg.message_id,
            "last_preview_time": 0,
        }
        game_sessions[chat_id] = session
        return session
    except Exception as e:
        logging.error(f"[ensure_session] failed: {e}", exc_info=True)
        return None


async def _process_snapshot(room: str, image_data: str, source: str) -> str:
    """
    Универсальная обработка превью.
    source: 'socket' / 'http' — чисто для логов.
    """
    if not room or not image_data:
        return "Bad Request"

    chat_id = get_chat_id_from_room(room)
    session = await _ensure_session(chat_id)
    if not session:
        return "No session"

    now = time.time()
    if now - session.get("last_preview_time", 0) < PREVIEW_UPDATE_INTERVAL:
        return "Skipped"

    msg_id = session.get("preview_message_id")
    if not msg_id:
        return "No preview_message_id"

    try:
        _header, encoded = image_data.split(",", 1)
        image_bytes = base64.b64decode(encoded)
    except Exception:
        return "Bad image"

    logging.info(f"📸 [{source}] Preview update for {chat_id} (b64={len(encoded)} chars)")

    media = InputMediaPhoto(
        media=BufferedInputFile(image_bytes, filename="preview.jpg"),
        caption=f"🎨 **LIVE:** {session.get('drawer_name', 'Player')}...",
        parse_mode="Markdown",
    )

    try:
        await bot.edit_message_media(
            media=media,
            chat_id=int(chat_id),
            message_id=msg_id,
        )
        session["last_preview_time"] = now
        return "OK"
    except Exception as e:
        if "message is not modified" in str(e).lower():
            session["last_preview_time"] = now
            return "Not modified"
        logging.error(f"[edit_message_media] {e}", exc_info=True)
        return "TG error"


# ================== SOCKET EVENTS ==================

@sio.event
async def connect(sid, environ):
    logging.info(f"[socket] CONNECT {sid}")


@sio.event
async def disconnect(sid):
    logging.info(f"[socket] DISCONNECT {sid}")


@sio.event
async def join_room(sid, data):
    room = str((data or {}).get("room") or "")
    sio.enter_room(sid, room)
    logging.info(f"[socket] JOIN {room}")


@sio.event
async def draw_step(sid, data):
    room = str((data or {}).get("room") or "")
    # логируем, чтобы видеть что реально летит рисование
    logging.info(f"[socket] draw_step room={room}")
    await sio.emit("draw_data", data, room=room, skip_sid=sid)


@sio.event
async def snapshot(sid, data):
    """
    Превью через socket.io (ACK возвращает строку результата).
    """
    room = str((data or {}).get("room") or "")
    image_data = (data or {}).get("image") or ""
    logging.info(f"📥 [socket] snapshot event room={room} size={len(image_data)}")
    return await _process_snapshot(room, image_data, source="socket")


@sio.event
async def skip_turn(sid, data):
    room = str((data or {}).get("room") or "")
    chat_id = get_chat_id_from_room(room)

    session = game_sessions.get(chat_id)
    new_w = random.choice(GAME_WORDS)
    if session:
        session["word"] = new_w

    await sio.emit("new_word_data", {"word": new_w}, room=room)


@sio.event
async def final_frame(sid, data):
    room = str((data or {}).get("room") or "")
    chat_id = get_chat_id_from_room(room)
    session = game_sessions.get(chat_id)
    if not session:
        return

    try:
        _header, encoded = (data or {}).get("image", "").split(",", 1)
        image_bytes = base64.b64decode(encoded)

        if session.get("preview_message_id"):
            try:
                await bot.delete_message(int(chat_id), session["preview_message_id"])
            except Exception:
                pass

        await bot.send_photo(
            chat_id=int(chat_id),
            photo=BufferedInputFile(image_bytes, filename="result.jpg"),
            caption=f"🏁 **Финиш!** Слово: {session.get('word', '???')}",
            parse_mode="Markdown",
        )

    except Exception as e:
        logging.error(f"[final_frame] {e}", exc_info=True)

    finally:
        game_sessions.pop(chat_id, None)


# ================== HTTP (index only) ==================

async def serve_index(request: web.Request):
    resp = web.FileResponse("index.html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ================== ROUTES ==================

app.router.add_get("/game", serve_index)
app.router.add_get("/game/", serve_index)


async def start_socket_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SOCKET_SERVER_HOST, SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"Server running on port {SOCKET_SERVER_PORT}")


# ================== BOT UI ==================

def get_game_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    room_param = str(chat_id).replace("-", "m") if chat_id < 0 else str(chat_id)
    v = int(time.time())  # ломаем кэш
    app_link = f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}?startapp={room_param}&v={v}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Открыть холст", url=app_link)],
            [
                InlineKeyboardButton(text="👁 Слово", callback_data=f"cr_w_{chat_id}"),
                InlineKeyboardButton(text="🔄 Другое", callback_data=f"cr_n_{chat_id}"),
            ],
        ]
    )


async def handle_start_game(message: types.Message):
    chat_id = message.chat.id
    word = random.choice(GAME_WORDS)

    await message.answer(
        f"🎮 **КРОКОДИЛ**\nВедущий: {message.from_user.full_name}",
        reply_markup=get_game_keyboard(chat_id),
        parse_mode="Markdown",
    )

    blank = base64.b64decode(BLANK_PNG_B64)
    prev = await message.answer_photo(
        BufferedInputFile(blank, "b.png"),
        caption="⏳ *Запуск...*",
        parse_mode="Markdown",
    )

    game_sessions[str(chat_id)] = {
        "word": word,
        "drawer_id": message.from_user.id,
        "drawer_name": message.from_user.full_name,
        "preview_message_id": prev.message_id,
        "last_preview_time": 0,
    }


async def handle_callback(cb: types.CallbackQuery):
    data = cb.data
    chat_id = data.split("_")[-1]

    session = game_sessions.get(chat_id)
    if not session:
        return await cb.answer("Игра не найдена")

    if data.startswith("cr_w_"):
        await cb.answer(f"Слово: {session['word'].upper()}", show_alert=True)

    elif data.startswith("cr_n_"):
        new_w = random.choice(GAME_WORDS)
        session["word"] = new_w

        room = f"m{chat_id.replace('-', '')}" if chat_id.startswith("-") else chat_id
        await sio.emit("new_word_data", {"word": new_w}, room=room)

        await cb.answer(f"Новое: {new_w.upper()}", show_alert=True)


# ================== ANSWER CHECK ==================

async def check_answer(msg: types.Message) -> bool:
    """
    True только если было правильное угадывание и мы обработали победу.
    """
    cid = str(msg.chat.id)
    sess = game_sessions.get(cid)

    if not sess or not msg.text:
        return False

    if msg.text.strip().lower() == str(sess.get("word", "")).strip().lower():
        # ведущий не должен сам угадывать — и НЕ должен блокировать остальное
        if msg.from_user and msg.from_user.id == sess.get("drawer_id"):
            return False

        await msg.answer(
            f"🎉 **{msg.from_user.full_name}** победил! Слово: **{sess['word']}**",
            parse_mode="Markdown",
        )

        if sess.get("preview_message_id"):
            try:
                await bot.delete_message(msg.chat.id, sess["preview_message_id"])
            except Exception:
                pass

        game_sessions.pop(cid, None)
        return True

    return False
