# crocodile.py
import asyncio
import base64
import logging
import os
import random
import time
import json
import html
from typing import Dict, Optional

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

# Как часто обновлять картинку в существующем сообщении
PREVIEW_UPDATE_INTERVAL = 2.5  # сек

# Как часто "поднимать" картинку вниз (переотправлять сообщением),
# чтобы догадки не уводили её далеко вверх.
# 0 = отключить.
BUMP_INTERVAL = 90  # сек (можешь менять)

# Сколько лидеров показывать после игры
LEADERBOARD_TOP = 10

# Файл со словами
WORDS_FILE = os.path.join(os.path.dirname(__file__), "crocowords.txt")

# Файл рейтингов
SCORES_FILE = os.path.join(os.path.dirname(__file__), "crocodile_scores.json")

BLANK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)

# chat_id(str) -> session dict
game_sessions: dict[str, dict] = {}

# chat_id(str) -> { user_id(str): {"pts": int, "name": str} }
_scores: Dict[str, Dict[str, dict]] = {}

# =============== socket.io server ===============
sio = socketio.AsyncServer(
    async_mode="aiohttp",
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=10 * 1024 * 1024,
)

app = web.Application(client_max_size=20 * 1024 * 1024)
sio.attach(app)


# ================== УТИЛИТЫ ==================

def get_chat_id_from_room(room: str) -> str:
    """
    room = tg start_param
    пример: m4611982229 -> -4611982229
    """
    room = str(room)
    if room.startswith("m"):
        return str(int(room.replace("m", "-")))
    return room


def _load_words() -> list[str]:
    """
    Читает слова из crocowords.txt.
    Формат: 1 слово/фраза на строку.
    Пустые строки и строки с # игнорируются.
    """
    try:
        if not os.path.exists(WORDS_FILE):
            logging.warning(f"[crocodile] Words file not found: {WORDS_FILE}")
            return ["кот", "дом", "лес", "кит", "сыр", "сок", "мяч", "жук", "зуб", "нос"]

        out = []
        with open(WORDS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    continue
                out.append(s)

        if not out:
            return ["кот", "дом", "лес", "кит", "сыр", "сок", "мяч", "жук", "зуб", "нос"]
        return out
    except Exception as e:
        logging.error(f"[crocodile] Failed to load words: {e}", exc_info=True)
        return ["кот", "дом", "лес", "кит", "сыр", "сок", "мяч", "жук", "зуб", "нос"]


def _pick_word() -> str:
    words = _load_words()
    return random.choice(words)


def _normalize_guess(s: str) -> str:
    # нормализация для угадывания: нижний регистр, пробелы схлопнуть
    return " ".join(s.strip().lower().split())


def _scores_load():
    """
    Поддержка старого и нового формата.
    Старый: {chat: {uid: pts_int}}
    Новый:  {chat: {uid: {"pts": int, "name": str}}}
    """
    global _scores
    try:
        if os.path.exists(SCORES_FILE):
            with open(SCORES_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        else:
            raw = {}

        normalized: Dict[str, Dict[str, dict]] = {}
        for cid, table in (raw or {}).items():
            normalized[str(cid)] = {}
            if not isinstance(table, dict):
                continue

            for uid, v in table.items():
                uid = str(uid)
                if isinstance(v, int):
                    normalized[str(cid)][uid] = {"pts": int(v), "name": ""}
                elif isinstance(v, dict):
                    pts = int(v.get("pts", 0))
                    name = str(v.get("name", "") or "")
                    normalized[str(cid)][uid] = {"pts": pts, "name": name}
                else:
                    normalized[str(cid)][uid] = {"pts": 0, "name": ""}

        _scores = normalized

    except Exception as e:
        logging.error(f"[scores] load failed: {e}", exc_info=True)
        _scores = {}


def _scores_save():
    try:
        with open(SCORES_FILE, "w", encoding="utf-8") as f:
            json.dump(_scores, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"[scores] save failed: {e}", exc_info=True)


def add_point(chat_id: str, user_id: int, user_name: str = ""):
    cid = str(chat_id)
    uid = str(user_id)

    if cid not in _scores:
        _scores[cid] = {}

    if uid not in _scores[cid]:
        _scores[cid][uid] = {"pts": 0, "name": ""}

    _scores[cid][uid]["pts"] = int(_scores[cid][uid].get("pts", 0)) + 1

    # обновляем имя, если передали (и если оно поменялось)
    if user_name:
        _scores[cid][uid]["name"] = str(user_name)

    _scores_save()


def format_leaderboard(chat_id: str, title: str = "🏆 Рейтинг игроков") -> str:
    cid = str(chat_id)
    table = _scores.get(cid, {})
    if not table:
        return f"{title}\n(пока пусто)"

    # сортируем по очкам desc
    items = sorted(
        table.items(),
        key=lambda x: int((x[1] or {}).get("pts", 0)),
        reverse=True,
    )[:LEADERBOARD_TOP]

    lines = [title]
    for i, (uid, data) in enumerate(items, start=1):
        pts = int((data or {}).get("pts", 0))
        name = ((data or {}).get("name") or "").strip() or "игрок"

        # HTML-экранирование имени (parse_mode="HTML"!)
        safe_name = html.escape(name)

        # Telegram понимает tg://user?id=
        lines.append(f'{i}. <a href="tg://user?id={uid}">{safe_name}</a> — <b>{pts}</b>')

    return "\n".join(lines)


async def _safe_delete_message(chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def _safe_edit_media(chat_id: int, message_id: int, image_bytes: bytes, caption: str):
    media = InputMediaPhoto(
        media=BufferedInputFile(image_bytes, filename="preview.jpg"),
        caption=caption,
        parse_mode="Markdown",
    )
    await bot.edit_message_media(
        media=media,
        chat_id=chat_id,
        message_id=message_id,
    )


async def _ensure_session(chat_id: str) -> Optional[dict]:
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
            "last_preview_bytes": blank,
            "bump_task": None,
        }
        game_sessions[chat_id] = session
        return session
    except Exception as e:
        logging.error(f"[ensure_session] failed: {e}", exc_info=True)
        return None


async def _stop_session(chat_id: str, reason: str = ""):
    """
    Останавливает сессию корректно:
    - отменяет bump_task
    - удаляет превью (если есть)
    - убирает из game_sessions
    """
    cid = str(chat_id)
    sess = game_sessions.get(cid)
    if not sess:
        return

    # cancel bump
    task = sess.get("bump_task")
    if task and isinstance(task, asyncio.Task) and not task.done():
        task.cancel()
        try:
            await task
        except Exception:
            pass

    # удалить превью-сообщение (последнее)
    if sess.get("preview_message_id"):
        await _safe_delete_message(int(cid), int(sess["preview_message_id"]))

    game_sessions.pop(cid, None)
    if reason:
        logging.info(f"[crocodile] session stopped chat={cid} reason={reason}")


async def _bump_loop(chat_id: str):
    """
    Переотправляет превью-картинку раз в BUMP_INTERVAL сек, чтобы она была внизу чата.
    ВАЖНО: это отдельное сообщение (send_photo), не edit_message_media.
    Старое сообщение удаляем, чтобы не плодить мусор.
    """
    if not BUMP_INTERVAL or BUMP_INTERVAL <= 0:
        return

    cid = str(chat_id)
    try:
        while True:
            await asyncio.sleep(BUMP_INTERVAL)

            sess = game_sessions.get(cid)
            if not sess:
                return

            img = sess.get("last_preview_bytes")
            if not img:
                # если ещё не было ни одного снапа — не бампим
                continue

            # удалим старое превью (чтобы не засорять чат)
            old_mid = sess.get("preview_message_id")
            if old_mid:
                await _safe_delete_message(int(cid), int(old_mid))

            # отправим новое
            msg = await bot.send_photo(
                int(cid),
                BufferedInputFile(img, "preview.jpg"),
                caption=f"🎨 *Ресует:* {sess.get('drawer_name','Player')}",
                parse_mode="Markdown",
            )
            sess["preview_message_id"] = msg.message_id
            sess["last_preview_time"] = time.time()

    except asyncio.CancelledError:
        return
    except Exception as e:
        logging.error(f"[bump_loop] {e}", exc_info=True)


async def _process_snapshot(room: str, image_data: str, source: str) -> str:
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
        header, encoded = image_data.split(",", 1)
        image_bytes = base64.b64decode(encoded)
    except Exception:
        return "Bad image"

    # сохраним последние байты для bump
    session["last_preview_bytes"] = image_bytes

    logging.info(f"📸 [{source}] Preview update for {chat_id} (b64={len(encoded)} chars)")

    try:
        await _safe_edit_media(
            chat_id=int(chat_id),
            message_id=int(msg_id),
            image_bytes=image_bytes,
            caption=f"🎨 **Ресует:** {session['drawer_name']}",
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
    new_w = _pick_word()
    if session:
        session["word"] = new_w

    await sio.emit("new_word_data", {"word": new_w}, room=room)


@sio.event
async def final_frame(sid, data):
    """
    Завершение игры кнопкой 🏁 в webapp
    """
    room = str(data.get("room"))
    chat_id = get_chat_id_from_room(room)
    session = game_sessions.get(chat_id)
    if not session:
        return

    try:
        header, encoded = data["image"].split(",", 1)
        image_bytes = base64.b64decode(encoded)

        # удаляем превью + останавливаем bump
        await _stop_session(chat_id, reason="final_frame")

        await bot.send_photo(
            chat_id=int(chat_id),
            photo=BufferedInputFile(image_bytes, filename="result.jpg"),
            caption=f"🏁 **Финиш!** Слово было: **{session['word']}**",
            parse_mode="Markdown",
        )

        # после завершения — покажем рейтинг
        await bot.send_message(
            int(chat_id),
            format_leaderboard(chat_id, "🏆 Самые умные педорасы"),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    except Exception as e:
        logging.error(f"[final_frame] {e}", exc_info=True)


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
                InlineKeyboardButton(text="🛑 Стоп", callback_data=f"cr_stop_{chat_id}"),
            ],
        ]
    )


async def handle_start_game(message: types.Message):
    # подгружаем рейтинг один раз
    if not _scores:
        _scores_load()

    chat_id = message.chat.id
    word = _pick_word()

    await message.answer(
        f"🎮 **КРАКАДИЛ**\nХуйдожник: {message.from_user.full_name}",
        reply_markup=get_game_keyboard(chat_id),
        parse_mode="Markdown",
    )

    blank = base64.b64decode(BLANK_PNG_B64)
    prev = await message.answer_photo(
        BufferedInputFile(blank, "b.png"),
        caption="⏳ *Запуск...*",
        parse_mode="Markdown",
    )

    cid = str(chat_id)
    game_sessions[cid] = {
        "word": word,
        "drawer_id": message.from_user.id,
        "drawer_name": message.from_user.full_name,
        "preview_message_id": prev.message_id,
        "last_preview_time": 0,
        "last_preview_bytes": blank,
        "bump_task": None,
    }

    # стартуем bump в фоне (если включен)
    if BUMP_INTERVAL and BUMP_INTERVAL > 0:
        game_sessions[cid]["bump_task"] = asyncio.create_task(_bump_loop(cid))


async def handle_callback(cb: types.CallbackQuery):
    data = cb.data
    chat_id = data.split("_")[-1]
    session = game_sessions.get(chat_id)
    if not session:
        return await cb.answer("Игра не найдена")

    if data.startswith("cr_w_"):
        await cb.answer(f"Слово: {session['word'].upper()}", show_alert=True)

    elif data.startswith("cr_n_"):
        new_w = _pick_word()
        session["word"] = new_w

        room = f"m{chat_id.replace('-', '')}" if chat_id.startswith("-") else chat_id
        await sio.emit("new_word_data", {"word": new_w}, room=room)

        await cb.answer(f"Новое: {new_w.upper()}", show_alert=True)

    elif data.startswith("cr_stop_"):
        await _stop_session(chat_id, reason="manual stop")
        await cb.message.answer("🛑 Игра остановлена.")
        await cb.message.answer(
            format_leaderboard(chat_id, "🏆 Рейтинг (текущий)"),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await cb.answer("Остановлено")


async def check_answer(msg: types.Message) -> bool:
    """
    Возвращает True если сообщение обработано крокодилом (т.е. угадали или это ведущий),
    чтобы main.py мог return и не обрабатывать дальше.
    """
    cid = str(msg.chat.id)
    sess = game_sessions.get(cid)

    if not sess or not msg.text:
        return False

    guess = _normalize_guess(msg.text)
    word = _normalize_guess(sess["word"])

    # ведущий пишет слово — не заканчиваем игру, но считаем сообщение обработанным
    if msg.from_user and msg.from_user.id == sess["drawer_id"] and guess == word:
        return True

    if guess == word:
        # начисляем балл + сохраняем имя для рейтинга
        if msg.from_user:
            add_point(cid, msg.from_user.id, msg.from_user.full_name)

        # победное сообщение
        await msg.answer(
            f"🎉 **{msg.from_user.full_name}** пабедил!\nСлово: **{sess['word']}**",
            parse_mode="Markdown",
        )

        # корректно остановим сессию (отменит bump и удалит превью)
        await _stop_session(cid, reason="guessed")

        # покажем рейтинг после игры
        await bot.send_message(
            int(cid),
            format_leaderboard(cid, "🏆 Самые умные педорасы"),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return True

    return False
