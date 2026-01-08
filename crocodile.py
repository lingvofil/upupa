import asyncio
import base64
import logging
import os
import random
import time
import json
import html
from typing import Dict, Set, Optional

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

BOT_USERNAME = "expertyebaniebot"
WEB_APP_SHORT_NAME = "upupadile"

SOCKET_SERVER_HOST = "0.0.0.0" # Changed to 0.0.0.0 to allow external connections if needed
SOCKET_SERVER_PORT = 8080

PREVIEW_UPDATE_INTERVAL = 2.5
WORDS_FILE = os.path.join(os.path.dirname(__file__), "crocowords.txt")
SCORES_FILE = os.path.join(os.path.dirname(__file__), "crocodile_scores.json")

BLANK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)

game_sessions: dict[str, dict] = {}
_scores: Dict[str, Dict[str, dict]] = {}

# Load scores from file on module import
if os.path.exists(SCORES_FILE):
    try:
        with open(SCORES_FILE, "r", encoding="utf-8") as f:
            _scores = json.load(f)
    except Exception as e:
        logging.error(f"Failed to load scores: {e}")

# ================= SOCKET =================
sio = socketio.AsyncServer(async_mode="aiohttp", cors_allowed_origins="*")
app = web.Application()
sio.attach(app)

async def start_socket_server():
    """
    This is the missing function that main.py calls.
    It starts the aiohttp runner for the Socket.IO server.
    """
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SOCKET_SERVER_HOST, SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"🚀 Socket.IO server started at http://{SOCKET_SERVER_HOST}:{SOCKET_SERVER_PORT}")
    
    # Keep the server running until the task is cancelled
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await runner.cleanup()

# ================= UTIL =================
def get_chat_id_from_room(room: str) -> str:
    return str(int(room.replace("m", "-"))) if room.startswith("m") else room

def _normalize_guess(s: str) -> str:
    return " ".join(s.strip().lower().split())

def _pick_word() -> str:
    if not os.path.exists(WORDS_FILE):
        # Fallback if file is missing
        return random.choice(["хуй", "упупа", "пидорас", "крокодил"])
    with open(WORDS_FILE, encoding="utf-8") as f:
        words = [x.strip() for x in f if x.strip() and not x.startswith("#")]
    return random.choice(words) if words else "пустота"

# ================= SCORE =================
def add_point(chat_id: str, user_id: int, name: str):
    cid = str(chat_id)
    uid = str(user_id)
    _scores.setdefault(cid, {})
    _scores[cid].setdefault(uid, {"pts": 0, "name": name})
    _scores[cid][uid]["pts"] += 1
    _scores[cid][uid]["name"] = name
    try:
        with open(SCORES_FILE, "w", encoding="utf-8") as f:
            json.dump(_scores, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Failed to save scores: {e}")

def format_leaderboard(chat_id: str, title: str) -> str:
    table = _scores.get(str(chat_id), {})
    if not table:
        return f"{title}\n(пока пусто)"
    items = sorted(table.items(), key=lambda x: x[1]["pts"], reverse=True)
    lines = [title]
    for i, (uid, d) in enumerate(items[:10], 1):
        lines.append(
            f'{i}. <a href="tg://user?id={uid}">{html.escape(d["name"])}</a> — <b>{d["pts"]}</b>'
        )
    return "\n".join(lines)

# ================= KEYBOARD =================
def final_keyboard(chat_id: str, likes: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"❤️ {likes}", callback_data=f"like_{chat_id}")],
            [InlineKeyboardButton(text="🎨 Хочу рисовать", callback_data=f"draw_{chat_id}")],
        ]
    )

# ================= SNAPSHOT =================
async def _process_snapshot(room: str, image_data: str):
    chat_id = get_chat_id_from_room(room)
    sess = game_sessions.get(chat_id)
    if not sess:
        return

    try:
        _, encoded = image_data.split(",", 1)
        img = base64.b64decode(encoded)
        sess["final_image_bytes"] = img

        now = time.time()
        if now - sess["last_preview_time"] < PREVIEW_UPDATE_INTERVAL:
            return

        await bot.edit_message_media(
            chat_id=int(chat_id),
            message_id=sess["preview_message_id"],
            media=InputMediaPhoto(
                media=BufferedInputFile(img, "preview.jpg"),
                caption=f"🎨 Рисует: {sess['drawer_name']}",
            ),
        )
        sess["last_preview_time"] = now
    except Exception as e:
        logging.error(f"Error processing snapshot for room {room}: {e}")

# ================= SOCKET EVENTS =================
@sio.event
async def join_room(sid, data):
    room = data.get("room")
    if room:
        sio.enter_room(sid, room)
        logging.info(f"Socket {sid} joined room {room}")

@sio.event
async def snapshot(sid, data):
    if "room" in data and "image" in data:
        await _process_snapshot(data["room"], data["image"])

# ================= BOT HANDLERS =================
async def handle_start_game(message: types.Message):
    chat_id = str(message.chat.id)
    word = _pick_word()
    blank = base64.b64decode(BLANK_PNG_B64)

    prev = await message.answer_photo(
        BufferedInputFile(blank, "b.png"),
        caption="⏳ Запуск...",
    )

    game_sessions[chat_id] = {
        "word": word,
        "drawer_id": message.from_user.id,
        "drawer_name": message.from_user.full_name,
        "preview_message_id": prev.message_id,
        "last_preview_time": 0,
        "final_image_bytes": None,
        "likes": set(),
        "final_message_id": None,
        "claimed_by": None,
    }
    
    # We could send a private message to the drawer with the word here
    try:
        await bot.send_message(message.from_user.id, f"Твоё слово: {word}\nРисуй в Web App!")
    except:
        await message.reply("Напиши мне в личку, чтобы я мог прислать тебе слово!")

async def check_answer(msg: types.Message) -> bool:
    cid = str(msg.chat.id)
    sess = game_sessions.get(cid)
    if not sess or not msg.text:
        return False

    if _normalize_guess(msg.text) != _normalize_guess(sess["word"]):
        return False

    # Stop current game by removing session immediately to prevent double winning
    current_sess = game_sessions.pop(cid)
    
    add_point(cid, msg.from_user.id, msg.from_user.full_name)

    await msg.answer(
        f"🎉 <b>{html.escape(msg.from_user.full_name)}</b> угадал!\nСлово: <b>{current_sess['word']}</b>",
        parse_mode="HTML",
    )

    img = current_sess.get("final_image_bytes")
    if img:
        msg_final = await bot.send_photo(
            int(cid),
            BufferedInputFile(img, "final.jpg"),
            caption=f"🎨 Хуйдожник: {current_sess['drawer_name']}",
            reply_markup=final_keyboard(cid, 0),
        )
        # We need to keep a record for likes, let's put it back in a passive state or separate dict
        # For simplicity, we'll store finished session info elsewhere if needed
        game_sessions[f"last_{cid}"] = current_sess
        game_sessions[f"last_{cid}"]["final_message_id"] = msg_final.message_id

    await bot.send_message(
        int(cid),
        format_leaderboard(cid, "🏆 Самые умные педорасы"),
        parse_mode="HTML",
    )

    return True

async def handle_callback(cb: types.CallbackQuery):
    data = cb.data
    cid = data.split("_", 1)[1]
    
    # Try to find active or just finished session
    sess = game_sessions.get(cid) or game_sessions.get(f"last_{cid}")
    
    if not sess:
        return await cb.answer("Игра завершена")

    if data.startswith("like_"):
        uid = cb.from_user.id
        if uid in sess["likes"]:
            return await cb.answer("Ты уже лайкал уебак")

        sess["likes"].add(uid)
        await bot.edit_message_reply_markup(
            chat_id=int(cb.message.chat.id),
            message_id=sess["final_message_id"],
            reply_markup=final_keyboard(cid, len(sess["likes"])),
        )
        return await cb.answer("❤️")

    if data.startswith("draw_"):
        if sess.get("claimed_by"):
            return await cb.answer("Уже занято")

        sess["claimed_by"] = cb.from_user.id
        await cb.message.answer(f"🎨 <b>{html.escape(cb.from_user.full_name)}</b> теперь рисует!", parse_mode="HTML")
        
        # Trigger new game logic
        await handle_start_game(cb.message)
        return await cb.answer("Ты ведущий")

async def handle_text_stop(message: types.Message):
    cid = str(message.chat.id)
    if cid in game_sessions:
        del game_sessions[cid]
        await message.answer("Игра остановлена.")
    else:
        await message.answer("А ничо и не запущено.")
