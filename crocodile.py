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

# Интервал обновления превью (сек)
PREVIEW_UPDATE_INTERVAL = 4.0 

# Пустой PNG 1x1 для старта
BLANK_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="

# ================== ХРАНИЛИЩЕ ==================
game_sessions: dict[str, dict] = {}

# ================== SOCKET.IO ==================
# Увеличиваем буфер на всякий случай, хотя мы будем сжимать на клиенте
sio = socketio.AsyncServer(
    async_mode="aiohttp",
    cors_allowed_origins="*",
    max_http_buffer_size=10 * 1024 * 1024, 
    ping_timeout=60,
)

app = web.Application()
sio.attach(app)

def get_chat_id_from_room(room: str) -> str:
    """Универсальное получение ID чата из комнаты"""
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
    """Прием сжатого превью"""
    try:
        room = str(data.get("room"))
        chat_id = get_chat_id_from_room(room)
        session = game_sessions.get(chat_id)

        if not session:
            return

        # Троттлинг (защита от частых обновлений)
        now = time.time()
        if now - session.get("last_preview_time", 0) < PREVIEW_UPDATE_INTERVAL:
            return

        msg_id = session.get("preview_message_id")
        if not msg_id:
            return

        # Логируем размер пакета (для отладки)
        img_str = data["image"]
        # logging.info(f"[DEBUG] Recv snapshot size: {len(img_str)} bytes")

        header, encoded = img_str.split(",", 1)
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
            pass
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

# ================== WEB SERVER ==================
async def serve_index(request: web.Request):
    return web.FileResponse("index.html")

app.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SOCKET_SERVER_HOST, SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"Socket server running at http://{SOCKET_SERVER_HOST}:{SOCKET_SERVER_PORT}")

# ================== GAME LOGIC ==================
async def generate_game_word() -> str:
    try:
        # Если есть модель:
        def sync_call():
             return model.generate_content("Придумай одно простое существительное для игры Крокодил.")
        response = await asyncio.to_thread(sync_call)
        w = response.text.strip().lower().split()[0]
        return "".join(filter(str.isalpha, w)) or "солнце"
    except:
        return random.choice(["арбуз", "дом", "дерево", "машина", "кот"])

def get_game_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    # Генерируем ссылку. Для групп добавляем префикс 'm' (minus), для лички - нет.
    # Если chat_id отрицательный -> m12345
    # Если chat_id положительный -> 12345
    
    room_param = str(chat_id).replace("-", "m") if chat_id < 0 else str(chat_id)
    app_link = f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}?startapp={room_param}"
    
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
        f"🎮 **КРОКОДИЛ**\nВедущий: {message.from_user.full_name}",
        reply_markup=get_game_keyboard(chat_id),
    )
    
    # Отправляем белый квадрат (PlaceHolder)
    blank_bytes = base64.b64decode(BLANK_PNG_B64)
    preview_msg = await message.answer_photo(
        photo=BufferedInputFile(blank_bytes, filename="blank.png"),
        caption="⏳ *Подготовка холста...*",
        parse_mode="Markdown"
    )

    game_sessions[str(chat_id)] = {
        "word": word,
        "drawer_id": message.from_user.id,
        "drawer_name": message.from_user.full_name,
        "preview_message_id": preview_msg.message_id,
        "last_preview_time": 0
    }

async def handle_callback(callback: types.CallbackQuery):
    data = callback.data
    chat_id = data.split("_")[-1]
    session = game_sessions.get(chat_id)

    if not session:
        return await callback.answer("Игра не активна")

    if callback.from_user.id != session["drawer_id"]:
        return await callback.answer("Только ведущий!", show_alert=True)

    if data.startswith("cr_w_"):
        await callback.answer(f"Слово: {session['word'].upper()}", show_alert=True)
    elif data.startswith("cr_n_"):
        new_word = await generate_game_word()
        session["word"] = new_word
        await callback.answer(f"Новое: {new_word.upper()}", show_alert=True)
        
        # Room ID logic
        room_param = f"m{chat_id.replace('-', '')}" if chat_id.startswith("-") else chat_id
        await sio.emit("new_word_data", {"word": new_word}, room=room_param)

async def check_answer(message: types.Message) -> bool:
    chat_id = str(message.chat.id)
    session = game_sessions.get(chat_id)

    if not session or not message.text: return False

    if message.text.strip().lower() == session["word"]:
        if message.from_user.id == session["drawer_id"]: return True

        await message.answer(f"🎉 **{message.from_user.full_name}** угадал! Это **{session['word'].upper()}**")
        
        if session.get("preview_message_id"):
            try: await bot.delete_message(message.chat.id, session["preview_message_id"])
            except: pass
        game_sessions.pop(chat_id, None)
        return True
    return False
