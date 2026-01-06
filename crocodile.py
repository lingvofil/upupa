import asyncio
import base64
import logging
import random
import time
from aiohttp import web
import socketio
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, InputMediaPhoto
from config import bot, model

# ================== НАСТРОЙКИ ==================
BOT_USERNAME = "expertyebaniebot"
WEB_APP_SHORT_NAME = "upupadile"
SOCKET_SERVER_HOST = "127.0.0.1"
SOCKET_SERVER_PORT = 8080
PREVIEW_UPDATE_INTERVAL = 3.0 

# Белый квадрат 1x1 для инициализации
BLANK_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="

# ================== ХРАНИЛИЩЕ ==================
game_sessions: dict[str, dict] = {}

# ================== SOCKET.IO ==================
sio = socketio.AsyncServer(
    async_mode="aiohttp",
    cors_allowed_origins="*",
    max_http_buffer_size=10 * 1024 * 1024,
)
app = web.Application()
sio.attach(app)

def get_chat_id_from_room(room: str) -> str:
    """Преобразует roomID (m12345) в chatID (-12345)"""
    room = str(room)
    if room.startswith("m"):
        return str(int(room.replace("m", "-")))
    return room

@sio.event
async def join_room(sid, data):
    room = str(data.get("room"))
    sio.enter_room(sid, room)
    logging.info(f"[socket] {sid} joined {room}")

@sio.event
async def draw_step(sid, data):
    room = str(data.get("room"))
    await sio.emit("draw_data", data, room=room, skip_sid=sid)

@sio.event
async def preview_snapshot(sid, data):
    """ОБНОВЛЕНИЕ КАРТИНКИ В ЧАТЕ"""
    try:
        room = str(data.get("room"))
        chat_id = get_chat_id_from_room(room)
        session = game_sessions.get(chat_id)

        if not session:
            logging.warning(f"[DEBUG] SESSION NOT FOUND for chat_id={chat_id}. Room={room}")
            return

        # Лимит обновлений
        now = time.time()
        if now - session.get("last_preview_time", 0) < PREVIEW_UPDATE_INTERVAL:
            return

        msg_id = session.get("preview_message_id")
        if not msg_id:
            logging.warning(f"[DEBUG] No preview_message_id for {chat_id}")
            return

        # Декодируем и обновляем
        logging.info(f"[DEBUG] Updating preview for {chat_id}...")
        header, encoded = data["image"].split(",", 1)
        image_bytes = base64.b64decode(encoded)
        
        media = InputMediaPhoto(
            media=BufferedInputFile(image_bytes, filename="preview.jpg"),
            caption=f"🎨 **LIVE:** {session['drawer_name']} рисует..."
        )
        
        await bot.edit_message_media(
            media=media,
            chat_id=int(chat_id),
            message_id=msg_id
        )
        session["last_preview_time"] = now

    except Exception as e:
        if "message is not modified" in str(e):
            pass # Это нормально, если картинка не изменилась
        else:
            logging.error(f"[DEBUG] Preview Error: {e}")

@sio.event
async def skip_turn(sid, data):
    room = str(data.get("room"))
    chat_id = get_chat_id_from_room(room)
    session = game_sessions.get(chat_id)
    
    if session:
        new_word = await generate_game_word()
        session["word"] = new_word
        await sio.emit("new_word_data", {"word": new_word}, room=room)
        logging.info(f"[GAME] Word skipped. New: {new_word}")

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
        
        if session.get("preview_message_id"):
            try: await bot.delete_message(chat_id, session["preview_message_id"])
            except: pass

        await bot.send_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(image_bytes, filename="result.jpg"),
            caption=f"🏁 **Стоп игра!**\nСлово было: **{session['word']}**"
        )
    except Exception as e:
        logging.error(f"Final Frame Error: {e}")
    finally:
        game_sessions.pop(chat_id, None)

# ================== WEB & LOGIC ==================
async def serve_index(request: web.Request):
    return web.FileResponse("index.html")

app.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SOCKET_SERVER_HOST, SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"Socket server running at {SOCKET_SERVER_HOST}:{SOCKET_SERVER_PORT}")

async def generate_game_word() -> str:
    try:
        # model.generate_content... (раскомментируйте, если есть API)
        # return "слово"
        def sync_call():
             return model.generate_content("Придумай существительное для игры Крокодил")
        response = await asyncio.to_thread(sync_call)
        w = response.text.strip().lower().split()[0]
        return "".join(filter(str.isalpha, w))
    except:
        return random.choice(["носорог", "вертолет", "пирамида", "ананас"])

def get_game_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    # Важно: m-префикс только для групп (отрицательные ID)
    safe_chat_id = str(chat_id).replace("-", "m")
    app_link = f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}?startapp={safe_chat_id}"
    
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Открыть холст", url=app_link)],
            [
                InlineKeyboardButton(text="👁 Слово", callback_data=f"cr_w_{chat_id}"),
                InlineKeyboardButton(text="🔄 Следующее", callback_data=f"cr_n_{chat_id}")
            ]
        ]
    )

async def handle_start_game(message: types.Message):
    chat_id = message.chat.id
    word = await generate_game_word()
    
    await message.answer(
        f"🎮 **ИГРА НАЧАЛАСЬ!**\nВедущий: {message.from_user.full_name}",
        reply_markup=get_game_keyboard(chat_id),
    )
    
    # Отправляем белый квадрат как фото
    blank_bytes = base64.b64decode(BLANK_PNG_B64)
    preview_msg = await message.answer_photo(
        photo=BufferedInputFile(blank_bytes, filename="blank.png"),
        caption="⏳ *Холст готов...*",
        parse_mode="Markdown"
    )

    game_sessions[str(chat_id)] = {
        "word": word,
        "drawer_id": message.from_user.id,
        "drawer_name": message.from_user.full_name,
        "preview_message_id": preview_msg.message_id,
        "last_preview_time": 0
    }
    logging.info(f"New session created for {chat_id}")

async def handle_callback(callback: types.CallbackQuery):
    data = callback.data
    chat_id = data.split("_")[-1]
    session = game_sessions.get(chat_id)

    if not session:
        return await callback.answer("Игра не найдена (перезапустите бота)")

    if callback.from_user.id != session["drawer_id"]:
        return await callback.answer("Только ведущий!", show_alert=True)

    if data.startswith("cr_w_"):
        await callback.answer(f"Слово: {session['word'].upper()}", show_alert=True)
    elif data.startswith("cr_n_"):
        new_word = await generate_game_word()
        session["word"] = new_word
        await callback.answer(f"Новое слово: {new_word.upper()}", show_alert=True)
        # room ID для сокета: mID для групп, ID для лички
        room_id = f"m{chat_id.replace('-', '')}" if chat_id.startswith("-") else chat_id
        await sio.emit("new_word_data", {"word": new_word}, room=room_id)

async def check_answer(message: types.Message) -> bool:
    chat_id = str(message.chat.id)
    session = game_sessions.get(chat_id)

    if not session or not message.text: return False

    if message.text.strip().lower() == session["word"]:
        if message.from_user.id == session["drawer_id"]: return True

        await message.answer(f"🎉 **{message.from_user.full_name}** угадал! Это **{session['word'].upper()}**")
        
        # Удаляем превью и сессию
        if session.get("preview_message_id"):
            try: await bot.delete_message(message.chat.id, session["preview_message_id"])
            except: pass
        game_sessions.pop(chat_id, None)
        return True
    return False
