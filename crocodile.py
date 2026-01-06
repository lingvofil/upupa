# crocodile.py
import base64
import logging
import random
import time
import asyncio
from pathlib import Path
from typing import Optional

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

# раз в сколько секунд разрешаем обновлять превью через edit_message_media
PREVIEW_UPDATE_INTERVAL = 2.5

# раз в сколько секунд переотправлять превью, чтобы оно снова было внизу чата
BUMP_INTERVAL = 90

# Где лежит файл со словами
# 1) /root/upupa/crocowords.txt (как ты хочешь)
# 2) или рядом с crocodile.py
WORDS_FILE_CANDIDATES = [
    Path("/root/upupa/crocowords.txt"),
    Path(__file__).with_name("crocowords.txt"),
]

# 1x1 PNG (белый)
BLANK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)

# fallback на случай пустого файла
FALLBACK_WORDS = [
    "кроссовки", "гипноз", "перфоратор", "электросамокат", "калькулятор",
    "телепорт", "скафандр", "радиатор", "канделябр", "парашют",
]

# chat_id(str) -> session dict
game_sessions: dict[str, dict] = {}

# кеш слов
_words_cache: list[str] = []
_words_mtime: float = 0.0


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
    room = str(room)
    if room.startswith("m"):
        return str(int(room.replace("m", "-")))
    return room


def _decode_data_url(image_data: str) -> Optional[bytes]:
    """data:image/jpeg;base64,... -> bytes"""
    try:
        _, encoded = image_data.split(",", 1)
        return base64.b64decode(encoded)
    except Exception:
        return None


def _find_words_file() -> Optional[Path]:
    for p in WORDS_FILE_CANDIDATES:
        if p.exists() and p.is_file():
            return p
    return None


def _load_words_from_file(force: bool = False) -> list[str]:
    """
    Читает crocowords.txt:
    - 1 слово/фраза на строку
    - пустые строки игнорируем
    - строки с # в начале — комментарии
    """
    global _words_cache, _words_mtime

    path = _find_words_file()
    if not path:
        return []

    try:
        st = path.stat()
        if (not force) and _words_cache and st.st_mtime == _words_mtime:
            return _words_cache

        raw = path.read_text(encoding="utf-8", errors="ignore")
        words: list[str] = []
        for line in raw.splitlines():
            w = line.strip()
            if not w:
                continue
            if w.startswith("#"):
                continue
            # ограничим мусор
            if len(w) < 2:
                continue
            words.append(w)

        # уникальные, но сохраняем “примерно” порядок
        seen = set()
        uniq = []
        for w in words:
            key = w.lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(w)

        _words_cache = uniq
        _words_mtime = st.st_mtime
        logging.info(f"[crocodile] Loaded {len(_words_cache)} words from {path}")
        return _words_cache

    except Exception as e:
        logging.error(f"[crocodile] Failed to read words file: {e}", exc_info=True)
        return []


async def get_new_word() -> str:
    words = _load_words_from_file()
    if words:
        return random.choice(words)
    return random.choice(FALLBACK_WORDS)


async def _ensure_session(chat_id: str) -> dict | None:
    """
    Если сессии нет — пробуем создать превью-сообщение (photo)
    """
    session = game_sessions.get(chat_id)
    if session:
        return session

    try:
        blank = base64.b64decode(BLANK_PNG_B64)
        new_msg = await bot.send_photo(
            int(chat_id),
            BufferedInputFile(blank, "blank.png"),
            caption="🔄 Reload",
        )
        session = {
            "word": "???",
            "drawer_id": 0,
            "drawer_name": "Player",
            "preview_message_id": new_msg.message_id,
            "last_preview_time": 0.0,
            "last_bump_time": 0.0,
            "last_preview_bytes": blank,
        }
        game_sessions[chat_id] = session
        return session
    except Exception as e:
        logging.error(f"[ensure_session] failed: {e}", exc_info=True)
        return None


async def _bump_preview_if_needed(chat_id: str, session: dict) -> None:
    """
    Переотправляем превью, чтобы оно оказалось внизу чата.
    Старое удаляем (если можем), чтобы не спамить.
    """
    now = time.time()
    last_bump = float(session.get("last_bump_time", 0.0))
    if now - last_bump < BUMP_INTERVAL:
        return

    msg_id = session.get("preview_message_id")
    media_bytes = session.get("last_preview_bytes")

    if not msg_id or not media_bytes:
        session["last_bump_time"] = now
        return

    try:
        try:
            await bot.delete_message(int(chat_id), int(msg_id))
        except Exception:
            pass

        caption = f"🎨 LIVE: {session.get('drawer_name','Player')}..."
        new_msg = await bot.send_photo(
            int(chat_id),
            BufferedInputFile(media_bytes, "preview.jpg"),
            caption=caption,
        )

        session["preview_message_id"] = new_msg.message_id
        session["last_bump_time"] = now
        logging.info(f"⬇️ [bump] preview re-sent chat={chat_id}")

    except Exception as e:
        logging.error(f"[bump] failed: {e}", exc_info=True)
        session["last_bump_time"] = now


async def _process_snapshot(room: str, image_data: str, source: str) -> str:
    if not room or not image_data:
        return "Bad Request"

    chat_id = get_chat_id_from_room(room)
    session = await _ensure_session(chat_id)
    if not session:
        return "No session"

    now = time.time()
    if now - float(session.get("last_preview_time", 0.0)) < PREVIEW_UPDATE_INTERVAL:
        await _bump_preview_if_needed(chat_id, session)
        return "Skipped"

    msg_id = session.get("preview_message_id")
    if not msg_id:
        return "No preview_message_id"

    image_bytes = _decode_data_url(image_data)
    if not image_bytes:
        return "Bad image"

    # сохраняем байты для bump
    session["last_preview_bytes"] = image_bytes

    logging.info(f"📸 [{source}] Preview update chat={chat_id} bytes={len(image_bytes)}")

    media = InputMediaPhoto(
        media=BufferedInputFile(image_bytes, filename="preview.jpg"),
        caption=f"🎨 LIVE: {session.get('drawer_name','Player')}...",
    )

    try:
        await bot.edit_message_media(
            media=media,
            chat_id=int(chat_id),
            message_id=int(msg_id),
        )
        session["last_preview_time"] = now

        await _bump_preview_if_needed(chat_id, session)
        return "OK"

    except Exception as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            session["last_preview_time"] = now
            await _bump_preview_if_needed(chat_id, session)
            return "Not modified"

        logging.error(f"[edit_message_media] {e}", exc_info=True)
        return "TG error"


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
    room = str(data.get("room"))
    await sio.emit("draw_data", data, room=room, skip_sid=sid)


@sio.event
async def snapshot(sid, data):
    room = str(data.get("room") or "")
    image_data = data.get("image") or ""
    logging.info(f"📥 [socket] snapshot event room={room} size={len(image_data)}")
    return await _process_snapshot(room, image_data, source="socket")


@sio.event
async def skip_turn(sid, data):
    room = str(data.get("room"))
    chat_id = get_chat_id_from_room(room)

    session = game_sessions.get(chat_id)
    new_w = await get_new_word()
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
        image_bytes = _decode_data_url(data.get("image", ""))
        if not image_bytes:
            return

        if session.get("preview_message_id"):
            try:
                await bot.delete_message(int(chat_id), int(session["preview_message_id"]))
            except Exception:
                pass

        await bot.send_photo(
            chat_id=int(chat_id),
            photo=BufferedInputFile(image_bytes, filename="result.jpg"),
            caption=f"🏁 Финиш! Слово: {session['word']}",
        )

    except Exception as e:
        logging.error(f"[final_frame] {e}", exc_info=True)

    finally:
        game_sessions.pop(chat_id, None)


# ================== HTTP ==================

async def serve_index(request: web.Request):
    resp = web.FileResponse("index.html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


app.router.add_get("/game", serve_index)
app.router.add_get("/game/", serve_index)


async def start_socket_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SOCKET_SERVER_HOST, SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"Server running on port {SOCKET_SERVER_PORT}")


# ================== BOT LOGIC ==================

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
    word = await get_new_word()

    await message.answer(
        f"🎮 **КРОКОДИЛ**\nВедущий: {message.from_user.full_name}",
        reply_markup=get_game_keyboard(chat_id),
        parse_mode="Markdown",
    )

    blank = base64.b64decode(BLANK_PNG_B64)
    prev = await message.answer_photo(
        BufferedInputFile(blank, "blank.png"),
        caption="⏳ *Запуск...*",
        parse_mode="Markdown",
    )

    game_sessions[str(chat_id)] = {
        "word": word,
        "drawer_id": message.from_user.id,
        "drawer_name": message.from_user.full_name,
        "preview_message_id": prev.message_id,
        "last_preview_time": 0.0,
        "last_bump_time": time.time(),
        "last_preview_bytes": blank,
    }


async def handle_callback(cb: types.CallbackQuery):
    data = cb.data
    chat_id = data.split("_")[-1]
    session = game_sessions.get(chat_id)
    if not session:
        return await cb.answer("Игра не найдена")

    if data.startswith("cr_w_"):
        await cb.answer(f"Слово: {str(session['word']).upper()}", show_alert=True)

    elif data.startswith("cr_n_"):
        new_w = await get_new_word()
        session["word"] = new_w

        room = f"m{chat_id.replace('-', '')}" if chat_id.startswith("-") else chat_id
        await sio.emit("new_word_data", {"word": new_w}, room=room)

        await cb.answer(f"Новое: {str(new_w).upper()}", show_alert=True)


async def check_answer(msg: types.Message) -> bool:
    cid = str(msg.chat.id)
    sess = game_sessions.get(cid)

    if not sess or not msg.text:
        return False

    if msg.text.strip().lower() == str(sess["word"]).strip().lower():
        if msg.from_user and msg.from_user.id == sess["drawer_id"]:
            return True

        await msg.answer(
            f"🎉 **{msg.from_user.full_name}** победил!\nСлово: **{sess['word']}**",
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
