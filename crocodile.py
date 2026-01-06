# crocodile.py
import base64
import logging
import random
import time
import asyncio
from typing import Optional

from aiohttp import web, ClientSession
import socketio

from aiogram import types
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
    InputMediaPhoto,
    InputMediaAnimation,
)

from config import bot

# ================== НАСТРОЙКИ ==================
BOT_USERNAME = "expertyebaniebot"
WEB_APP_SHORT_NAME = "upupadile"

SOCKET_SERVER_HOST = "127.0.0.1"
SOCKET_SERVER_PORT = 8080

# как часто разрешаем редактировать превью (сек)
PREVIEW_UPDATE_INTERVAL = 2.5

# как часто "поднимать" превью в чат (сек)
# (переотправка сообщения, чтобы оно снова было внизу)
BUMP_INTERVAL = 90

# 1x1 прозрачный GIF
BLANK_GIF_B64 = "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="

# fallback словарь (RU) — используется если API недоступен
FALLBACK_WORDS_RU = [
    "электросамокат", "перфоратор", "самогонный аппарат", "пылесос", "пижама",
    "парашют", "канделябр", "песочные часы", "гравитация", "бумеранг",
    "кроссовки", "термос", "сковородка", "бронежилет", "радиатор",
    "алгоритм", "компостер", "гипноз", "фейерверк", "калькулятор",
    "фломастер", "карантин", "профессор", "телепорт", "аквариум",
    "скафандр", "шахматист", "бариста", "пилот", "дирижёр",
    "пианист", "инкассатор", "метеорит", "кочерга", "пингвин",
    "крокодил", "пирамида", "экскаватор", "светофор", "хамелеон",
]

# Datamuse — простой сервис слов (англ). Тянем “сложнее” по длине/частоте
DATAMUSE_URL = "https://api.datamuse.com/words"
DATAMUSE_MIN_LEN = 6
DATAMUSE_MAX_LEN = 14
DATAMUSE_FETCH_N = 40

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
    room = str(room)
    if room.startswith("m"):
        return str(int(room.replace("m", "-")))
    return room


def _decode_data_url(image_data: str) -> Optional[tuple[str, bytes]]:
    """
    Возвращает (mime, bytes) из dataURL: data:image/gif;base64,...
    """
    try:
        header, encoded = image_data.split(",", 1)
        raw = base64.b64decode(encoded)
        mime = "application/octet-stream"
        if header.startswith("data:") and ";base64" in header:
            mime = header.split(";", 1)[0].replace("data:", "").strip()
        return mime, raw
    except Exception:
        return None


async def _fetch_words_datamuse() -> list[str]:
    """
    Забираем список слов (англ) из Datamuse.
    Берем "сложнее": длиннее, плюс стараемся убирать очень частотные.
    """
    # идеи запросов: темы/подборки, чтобы было разнообразнее
    topics = ["technology", "science", "animals", "movies", "sports", "music", "history", "space"]
    topic = random.choice(topics)

    params = {
        "topics": topic,
        "max": str(DATAMUSE_FETCH_N),
    }

    words: list[str] = []
    try:
        async with ClientSession() as session:
            async with session.get(DATAMUSE_URL, params=params, timeout=8) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                for item in data:
                    w = (item.get("word") or "").strip()
                    if not w:
                        continue
                    if " " in w or "-" in w:
                        continue
                    if not (DATAMUSE_MIN_LEN <= len(w) <= DATAMUSE_MAX_LEN):
                        continue
                    # простая фильтрация “слишком простых”
                    if w.lower() in {"animal", "people", "thing"}:
                        continue
                    words.append(w.lower())
    except Exception:
        return []

    # если мало — пробуем второй раз другой topic
    if len(words) < 10:
        try:
            params["topics"] = random.choice(topics)
            async with ClientSession() as session:
                async with session.get(DATAMUSE_URL, params=params, timeout=8) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data:
                            w = (item.get("word") or "").strip()
                            if not w or " " in w or "-" in w:
                                continue
                            if not (DATAMUSE_MIN_LEN <= len(w) <= DATAMUSE_MAX_LEN):
                                continue
                            words.append(w.lower())
        except Exception:
            pass

    # уникальные
    return sorted(set(words))


async def _get_new_word() -> str:
    """
    Получить новое слово:
    1) пытаемся Datamuse
    2) fallback RU список
    """
    remote = await _fetch_words_datamuse()
    if remote:
        return random.choice(remote)
    return random.choice(FALLBACK_WORDS_RU)


async def _ensure_session(chat_id: str) -> dict | None:
    """
    Если сессии нет — пробуем восстановить (создать превью как GIF-анимацию)
    """
    session = game_sessions.get(chat_id)
    if session:
        return session

    try:
        blank = base64.b64decode(BLANK_GIF_B64)
        new_msg = await bot.send_animation(
            int(chat_id),
            BufferedInputFile(blank, "blank.gif"),
            caption="🔄 Reload",
        )
        session = {
            "word": "???",
            "drawer_id": 0,
            "drawer_name": "Player",
            "preview_message_id": new_msg.message_id,
            "last_preview_time": 0.0,
            "last_bump_time": 0.0,
            "last_preview_bytes": blank,  # держим последнее превью, чтобы можно было переотправить
            "last_preview_mime": "image/gif",
        }
        game_sessions[chat_id] = session
        return session
    except Exception as e:
        logging.error(f"[ensure_session] failed: {e}", exc_info=True)
        return None


async def _bump_preview_if_needed(chat_id: str, session: dict) -> None:
    """
    Поднимаем превью вниз чата:
    - удаляем старое превью-сообщение
    - отправляем новое с последним медиа
    """
    now = time.time()
    last_bump = float(session.get("last_bump_time", 0.0))
    if now - last_bump < BUMP_INTERVAL:
        return

    msg_id = session.get("preview_message_id")
    media_bytes = session.get("last_preview_bytes")
    media_mime = session.get("last_preview_mime", "image/gif")

    if not msg_id or not media_bytes:
        session["last_bump_time"] = now
        return

    try:
        # удаляем старое (чтобы не плодить)
        try:
            await bot.delete_message(int(chat_id), int(msg_id))
        except Exception:
            pass

        caption = f"🎨 LIVE: {session.get('drawer_name','Player')}..."

        if media_mime == "image/gif":
            new_msg = await bot.send_animation(
                int(chat_id),
                BufferedInputFile(media_bytes, "preview.gif"),
                caption=caption,
            )
        else:
            new_msg = await bot.send_photo(
                int(chat_id),
                BufferedInputFile(media_bytes, "preview.jpg"),
                caption=caption,
            )

        session["preview_message_id"] = new_msg.message_id
        session["last_bump_time"] = now
        logging.info(f"⬇️ [bump] preview re-sent for chat={chat_id}")

    except Exception as e:
        # не критично
        logging.error(f"[bump] failed: {e}", exc_info=True)
        session["last_bump_time"] = now


async def _process_snapshot(room: str, image_data: str, source: str) -> str:
    if not room or not image_data:
        return "Bad Request"

    chat_id = get_chat_id_from_room(room)
    session = await _ensure_session(chat_id)
    if not session:
        return "No session"

    # throttling на редактирование
    now = time.time()
    if now - float(session.get("last_preview_time", 0.0)) < PREVIEW_UPDATE_INTERVAL:
        # но bump проверим отдельно (если давно)
        await _bump_preview_if_needed(chat_id, session)
        return "Skipped"

    msg_id = session.get("preview_message_id")
    if not msg_id:
        return "No preview_message_id"

    decoded = _decode_data_url(image_data)
    if not decoded:
        return "Bad image"

    mime, image_bytes = decoded

    logging.info(f"📸 [{source}] Preview update chat={chat_id} mime={mime} bytes={len(image_bytes)}")

    try:
        # сохраняем "последнее" — для bump
        session["last_preview_bytes"] = image_bytes
        session["last_preview_mime"] = mime

        if mime == "image/gif":
            media = InputMediaAnimation(
                media=BufferedInputFile(image_bytes, filename="preview.gif"),
                caption=f"🎨 LIVE: {session.get('drawer_name','Player')}...",
            )
        else:
            media = InputMediaPhoto(
                media=BufferedInputFile(image_bytes, filename="preview.jpg"),
                caption=f"🎨 LIVE: {session.get('drawer_name','Player')}...",
            )

        await bot.edit_message_media(
            media=media,
            chat_id=int(chat_id),
            message_id=int(msg_id),
        )

        session["last_preview_time"] = now

        # после успешного апдейта — иногда bump (если чат уехал)
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
    new_w = await _get_new_word()
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
        decoded = _decode_data_url(data.get("image", ""))
        if not decoded:
            return
        _, image_bytes = decoded

        # удаляем превью
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
    v = int(time.time())
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
    word = await _get_new_word()

    await message.answer(
        f"🎮 **КРОКОДИЛ**\nВедущий: {message.from_user.full_name}",
        reply_markup=get_game_keyboard(chat_id),
        parse_mode="Markdown",
    )

    blank = base64.b64decode(BLANK_GIF_B64)
    prev = await message.answer_animation(
        BufferedInputFile(blank, "blank.gif"),
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
        "last_preview_mime": "image/gif",
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
        new_w = await _get_new_word()
        session["word"] = new_w

        room = f"m{chat_id.replace('-', '')}" if chat_id.startswith("-") else chat_id
        await sio.emit("new_word_data", {"word": new_w}, room=room)

        await cb.answer(f"Новое: {new_w.upper()}", show_alert=True)


async def check_answer(msg: types.Message) -> bool:
    cid = str(msg.chat.id)
    sess = game_sessions.get(cid)

    if not sess or not msg.text:
        return False

    # сравнение по lower
    if (msg.text or "").strip().lower() == str(sess["word"]).strip().lower():
        # ведущий не угадывает
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
