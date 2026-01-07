# crocodile.py
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

SOCKET_SERVER_HOST = "127.0.0.1"
SOCKET_SERVER_PORT = 8080

PREVIEW_UPDATE_INTERVAL = 2.5
WORDS_FILE = os.path.join(os.path.dirname(__file__), "crocowords.txt")
SCORES_FILE = os.path.join(os.path.dirname(__file__), "crocodile_scores.json")

BLANK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)

game_sessions: dict[str, dict] = {}
_scores: Dict[str, Dict[str, dict]] = {}

# ================= SOCKET =================
sio = socketio.AsyncServer(async_mode="aiohttp", cors_allowed_origins="*")
app = web.Application()
sio.attach(app)

# ================= UTIL =================
def get_chat_id_from_room(room: str) -> str:
    return str(int(room.replace("m", "-"))) if room.startswith("m") else room

def _normalize_guess(s: str) -> str:
    return " ".join(s.strip().lower().split())

def _pick_word() -> str:
    with open(WORDS_FILE, encoding="utf-8") as f:
        words = [x.strip() for x in f if x.strip() and not x.startswith("#")]
    return random.choice(words)

# ================= SCORE =================
def add_point(chat_id: str, user_id: int, name: str):
    cid = str(chat_id)
    uid = str(user_id)
    _scores.setdefault(cid, {})
    _scores[cid].setdefault(uid, {"pts": 0, "name": name})
    _scores[cid][uid]["pts"] += 1
    _scores[cid][uid]["name"] = name
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(_scores, f, ensure_ascii=False, indent=2)

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

# ================= SOCKET EVENTS =================
@sio.event
async def join_room(sid, data):
    sio.enter_room(sid, data["room"])

@sio.event
async def snapshot(sid, data):
    await _process_snapshot(data["room"], data["image"])

# ================= BOT =================
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

async def check_answer(msg: types.Message) -> bool:
    cid = str(msg.chat.id)
    sess = game_sessions.get(cid)
    if not sess or not msg.text:
        return False

    if _normalize_guess(msg.text) != _normalize_guess(sess["word"]):
        return False

    add_point(cid, msg.from_user.id, msg.from_user.full_name)

    await msg.answer(
        f"🎉 **{msg.from_user.full_name}** угадал!\nСлово: **{sess['word']}**",
        parse_mode="Markdown",
    )

    img = sess.get("final_image_bytes")
    if img:
        msg_final = await bot.send_photo(
            int(cid),
            BufferedInputFile(img, "final.jpg"),
            caption=f"🎨 Хуйдожник: {sess['drawer_name']}",
            reply_markup=final_keyboard(cid, 0),
        )
        sess["final_message_id"] = msg_final.message_id

    await bot.send_message(
        int(cid),
        format_leaderboard(cid, "🏆 Самые умные педорасы"),
        parse_mode="HTML",
    )

    return True

# ================= CALLBACK =================
async def handle_callback(cb: types.CallbackQuery):
    data = cb.data
    cid = data.split("_", 1)[1]
    sess = game_sessions.get(cid)
    if not sess:
        return await cb.answer("Игра завершена")

    # LIKE
    if data.startswith("like_"):
        uid = cb.from_user.id
        if uid in sess["likes"]:
            return await cb.answer("Ты уже лайкал уебак")

        sess["likes"].add(uid)
        await bot.edit_message_reply_markup(
            int(cid),
            sess["final_message_id"],
            reply_markup=final_keyboard(cid, len(sess["likes"])),
        )
        await cb.message.answer(
            f"{cb.from_user.full_name} поставил лайк хуйдожнику"
        )
        return await cb.answer("❤️")

    # CLAIM DRAWER
    if data.startswith("draw_"):
        if sess["claimed_by"]:
            return await cb.answer("Уже занято")

        sess["claimed_by"] = cb.from_user.id

        await cb.message.answer(
            f"🎨 **{cb.from_user.full_name}** теперь рисует!",
            parse_mode="Markdown",
        )

        await handle_start_game(
            types.Message(
                chat=cb.message.chat,
                from_user=cb.from_user,
                message_id=0,
                date=None,
            )
        )
        return await cb.answer("Ты ведущий")
