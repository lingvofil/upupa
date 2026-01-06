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
SOCKET_SERVER_HOST = "127.0.0.1"
SOCKET_SERVER_PORT = 8080

PREVIEW_UPDATE_INTERVAL = 2.5  # сек (троттлинг превью)

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
)

# Лимит 20MB
app = web.Application(client_max_size=20 * 1024 * 1024)
sio.attach(app)


def get_chat_id_from_room(room: str) -> str:
    """
    room = tg start_param
    пример: m4611982229 -> -4611982229
    """
    room = str(room)
    if room.startswith("m"):
        return str(int(room.replace("m", "-")))
    return room


# ================== SOCKET EVENTS ==================

@sio.event
async def connect(sid, environ):
    logging.info(f"[socket] CONNECT {sid}")


@sio.event
async def join_room(sid, data):
    room = str(data.get("room"))
    sio.enter_room(sid, room)
    logging.info(f"[socket] JOIN {room}")


@sio.event
async def draw_step(sid, data):
    # ретранслируем шаг рисования
    room = str(data.get("room"))
    await sio.emit("draw_data", data, room=room, skip_sid=sid)


@sio.event
async def skip_turn(sid, data):
    room = str(data.get("room"))
    chat_id = get_chat_id_from_room(room)

    session = game_sessions.get(chat_id)
    new_w = random.choice(GAME_WORDS)
    if session:
        session["word"] = new_w

    await sio.emit("new_word_data", {"word": new_w}, room=room)


@sio.event
async def final_frame(sid, data):
    room = str(data.get("room"))
    chat_id = get_chat_id_from_room(room)
    session = game_sessions.get(chat_id)
    if not session:
        return

    try:
        header, encoded = data["image"].split(",", 1)
        image_bytes = base64.b64decode(encoded)

        # удаляем preview, если есть
        if session.get("preview_message_id"):
            try:
                await bot.delete_message(chat_id, session["preview_message_id"])
            except Exception:
                pass

        await bot.send_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(image_bytes, filename="result.jpg"),
            caption=f"🏁 **Финиш!** Слово: {session['word']}",
            parse_mode="Markdown",
        )

    except Exception as e:
        logging.error(f"[final_frame] {e}")

    finally:
        game_sessions.pop(chat_id, None)


# ================== HTTP SNAPSHOT ==================

async def handle_snapshot_upload(request: web.Request):
    """
    Принимает превью-картинку через POST и обновляет preview_message.
    """
    # Важно: логируем вход сразу, чтобы увидеть, что запрос ДОШЁЛ
    logging.info(
        f"📥 [HTTP] snapshot hit: {request.method} {request.path} len={request.content_length}"
    )

    try:
        data = await request.json()
        room = data.get("room")
        image_data = data.get("image")

        if not room or not image_data:
            return web.Response(text="Bad Request", status=400)

        chat_id = get_chat_id_from_room(room)
        session = game_sessions.get(chat_id)

        # --- Auto Recovery (если потеряли сессию, но клиент шлёт превью) ---
        if not session:
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
            except Exception as e:
                logging.error(f"[HTTP] Auto Recovery failed: {e}")
                return web.Response(text="No session", status=404)

        # Throttling
        now = time.time()
        if now - session.get("last_preview_time", 0) < PREVIEW_UPDATE_INTERVAL:
            return web.Response(text="Skipped", status=200)

        msg_id = session.get("preview_message_id")
        header, encoded = image_data.split(",", 1)
        image_bytes = base64.b64decode(encoded)

        logging.info(f"📸 [HTTP] Preview update for {chat_id} ({len(encoded)} b64 chars)")

        media = InputMediaPhoto(
            media=BufferedInputFile(image_bytes, filename="preview.jpg"),
            caption=f"🎨 **LIVE:** {session['drawer_name']}...",
            parse_mode="Markdown",
        )

        await bot.edit_message_media(
            media=media,
            chat_id=int(chat_id),
            message_id=msg_id,
        )

        session["last_preview_time"] = now
        return web.Response(text="OK", status=200)

    except Exception as e:
        # часто бывает при одинаковых данных
        if "message is not modified" not in str(e).lower():
            logging.error(f"[HTTP ERR] {e}")
        return web.Response(text="Error", status=500)


async def serve_index(request: web.Request):
    return web.FileResponse("index.html")


# ================== ROUTES ==================

# WebApp (и /game и /game/)
app.router.add_get("/game", serve_index)
app.router.add_get("/game/", serve_index)

# Snapshot endpoints: основной /game/snapshot + запасной /snapshot
app.router.add_post("/game/snapshot", handle_snapshot_upload)
app.router.add_post("/snapshot", handle_snapshot_upload)

# CORS for snapshot
async def options_handler(request: web.Request):
    return web.Response(
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )

app.router.add_options("/game/snapshot", options_handler)
app.router.add_options("/snapshot", options_handler)


async def start_socket_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SOCKET_SERVER_HOST, SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"Server running on port {SOCKET_SERVER_PORT}")


# ================== BOT LOGIC ==================

def get_game_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    room_param = str(chat_id).replace("-", "m") if chat_id < 0 else str(chat_id)
    app_link = f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}?startapp={room_param}"
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


async def check_answer(msg: types.Message) -> bool:
    cid = str(msg.chat.id)
    sess = game_sessions.get(cid)

    if not sess or not msg.text:
        return False

    if msg.text.strip().lower() == sess["word"]:
        # ведущий не должен сам угадывать
        if msg.from_user.id == sess["drawer_id"]:
            return True

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
