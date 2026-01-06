import asyncio
import base64
import logging
import random
import time
from aiohttp import web
import socketio
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, InputMediaPhoto
from config import bot

# ================== НАСТРОЙКИ ==================
BOT_USERNAME = "expertyebaniebot"
WEB_APP_SHORT_NAME = "upupadile"
SOCKET_SERVER_HOST = "127.0.0.1"
SOCKET_SERVER_PORT = 8080

# Интервал обновления превью (сек)
PREVIEW_UPDATE_INTERVAL = 3.0 

# Пустой PNG 1x1
BLANK_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="

# Список слов (чтобы не тратить лимиты AI и не зависеть от сайтов)
GAME_WORDS = [
    "арбуз", "барабан", "велосипед", "гриб", "дом", "еж", "жираф", "зонт", "игла", "кактус",
    "лампа", "машина", "ножницы", "очки", "паук", "робот", "солнце", "телефон", "улитка", "флаг",
    "хлеб", "цветок", "чайник", "шар", "щетка", "яблоко", "автобус", "банан", "вертолет", "груша",
    "дельфин", "елка", "жук", "замок", "индюк", "карандаш", "лодка", "мороженое", "носок", "облако",
    "пингвин", "ракета", "снеговик", "тыква", "утюг", "фонарь", "холодильник", "цыпленок", "черепаха", "шляпа",
    "экскаватор", "юла", "якорь", "ананас", "бабочка", "виноград", "гитара", "дверь", "енот", "желудь",
    "змея", "игрушка", "книга", "лимон", "мяч", "ноутбук", "орех", "пицца", "рыба", "самолет",
    "торт", "утка", "фотоаппарат", "хомяк", "циркуль", "часы", "шахматы", "щука", "эскимо", "юбка"
]

# ================== ХРАНИЛИЩЕ ==================
game_sessions: dict[str, dict] = {}

# ================== SOCKET.IO ==================
sio = socketio.AsyncServer(
    async_mode="aiohttp",
    cors_allowed_origins="*",
    max_http_buffer_size=5 * 1024 * 1024, # 5MB limit
    ping_timeout=30,
    ping_interval=10
)

app = web.Application()
sio.attach(app)

def get_chat_id_from_room(room: str) -> str:
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
            # logging.warning(f"[DEBUG] No session for {chat_id}")
            return

        now = time.time()
        # Пропускаем, если слишком часто
        if now - session.get("last_preview_time", 0) < PREVIEW_UPDATE_INTERVAL:
            return

        msg_id = session.get("preview_message_id")
        if not msg_id:
            return

        img_str = data.get("image", "")
        if not img_str: 
            return

        # logging.info(f"[DEBUG] Processing snapshot for {chat_id}, size={len(img_str)}")

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
        if "message is not modified" in str(e).lower():
            pass
        else:
            logging.error(f"[preview_snapshot] Error: {e}")

@sio.event
async def skip_turn(sid, data):
    room = str(data.get("room"))
    chat_id = get_chat_id_from_room(room)
    session = game_sessions.get(chat_id)
    
    if session:
        new_word = random.choice(GAME_WORDS)
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
def get_game_keyboard(chat_id: int) -> InlineKeyboardMarkup:
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
    word = random.choice(GAME_WORDS)
    
    await message.answer(
        f"🎮 **КРОКОДИЛ**\nВедущий: {message.from_user.full_name}",
        reply_markup=get_game_keyboard(chat_id),
    )
    
    # Отправляем белый квадрат (Placeholder)
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
        new_word = random.choice(GAME_WORDS)
        session["word"] = new_word
        await callback.answer(f"Новое: {new_word.upper()}", show_alert=True)
        
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
