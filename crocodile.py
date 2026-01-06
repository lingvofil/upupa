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
PREVIEW_UPDATE_INTERVAL = 4.0 

BLANK_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="

GAME_WORDS = [
    "кот", "дом", "кит", "лес", "лук", "мяч", "нос", "оса", "рак", "сок",
    "суп", "сыр", "ток", "бык", "вол", "год", "дед", "дуб", "жук", "зуб"
]

game_sessions: dict[str, dict] = {}

# Увеличиваем таймауты для медленного интернета
sio = socketio.AsyncServer(
    async_mode="aiohttp",
    cors_allowed_origins="*",
    max_http_buffer_size=5 * 1024 * 1024,
    ping_timeout=60,
    ping_interval=25
)

app = web.Application()
sio.attach(app)

def get_chat_id_from_room(room: str) -> str:
    room = str(room)
    if room.startswith("m"):
        return str(int(room.replace("m", "-")))
    return room

@sio.event
async def connect(sid, environ):
    logging.info(f"[socket] CONNECT {sid}")

@sio.event
async def disconnect(sid):
    logging.info(f"[socket] DISCONNECT {sid}")

@sio.event
async def join_room(sid, data):
    room = str(data.get("room"))
    sio.enter_room(sid, room)
    logging.info(f"[socket] {sid} JOINED {room}")

@sio.event
async def client_test(sid, data):
    """Тестовый пакет от кнопки TEST"""
    logging.info(f"✅ [DEBUG] RECEIVED TEST PACKET FROM {sid}: {data}")

@sio.event
async def draw_step(sid, data):
    room = str(data.get("room"))
    # Логируем каждый 10-й штрих, чтобы не спамить, но видеть активность
    # logging.info(f"[draw] step in {room}") 
    await sio.emit("draw_data", data, room=room, skip_sid=sid)

@sio.event
async def preview_snapshot(sid, data):
    try:
        room = str(data.get("room"))
        chat_id = get_chat_id_from_room(room)
        
        # === АВТО-ВОССТАНОВЛЕНИЕ СЕССИИ ===
        # Если сессия потерялась (перезапуск бота), создаем "временную"
        session = game_sessions.get(chat_id)
        if not session:
            logging.warning(f"[RECOVERY] Session lost for {chat_id}. Creating new one...")
            # Пытаемся отправить новое сообщение в чат
            try:
                blank = base64.b64decode(BLANK_PNG_B64)
                new_msg = await bot.send_photo(
                    chat_id=int(chat_id),
                    photo=BufferedInputFile(blank, "b.png"),
                    caption="🔄 **Сессия восстановлена**"
                )
                session = {
                    "word": "???",
                    "drawer_id": 0, # Неизвестен
                    "drawer_name": "Игрок",
                    "preview_message_id": new_msg.message_id,
                    "last_preview_time": 0
                }
                game_sessions[chat_id] = session
            except Exception as e:
                logging.error(f"[RECOVERY FAILED] {e}")
                return

        now = time.time()
        if now - session.get("last_preview_time", 0) < PREVIEW_UPDATE_INTERVAL:
            return

        msg_id = session.get("preview_message_id")
        
        img_str = data.get("image", "")
        logging.info(f"📸 [SNAPSHOT] Recv size: {len(img_str)} bytes for {chat_id}")

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
        if "message is not modified" not in str(e).lower():
            logging.error(f"Preview Error: {e}")

@sio.event
async def skip_turn(sid, data):
    room = str(data.get("room"))
    chat_id = get_chat_id_from_room(room)
    session = game_sessions.get(chat_id)
    new_w = random.choice(GAME_WORDS)
    if session: session["word"] = new_w
    await sio.emit("new_word_data", {"word": new_w}, room=room)
    logging.info(f"[GAME] New word: {new_w}")

@sio.event
async def final_frame(sid, data):
    room = str(data.get("room"))
    chat_id = get_chat_id_from_room(room)
    session = game_sessions.get(chat_id)
    if not session: return

    try:
        header, encoded = data["image"].split(",", 1)
        image_bytes = base64.b64decode(encoded)
        if session.get("preview_message_id"):
            try: await bot.delete_message(chat_id, session["preview_message_id"])
            except: pass

        await bot.send_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(image_bytes, filename="result.jpg"),
            caption=f"🏁 **Финиш!** Слово: {session['word']}"
        )
    except: pass
    finally:
        game_sessions.pop(chat_id, None)

# ================== SERVER ==================
async def serve_index(request: web.Request):
    return web.FileResponse("index.html")

app.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SOCKET_SERVER_HOST, SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"Socket server running on port {SOCKET_SERVER_PORT}")

# ================== LOGIC ==================
def get_game_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    room_param = str(chat_id).replace("-", "m") if chat_id < 0 else str(chat_id)
    app_link = f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}?startapp={room_param}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Открыть холст", url=app_link)],
        [InlineKeyboardButton(text="👁 Слово", callback_data=f"cr_w_{chat_id}"),
         InlineKeyboardButton(text="🔄 Другое", callback_data=f"cr_n_{chat_id}")]
    ])

async def handle_start_game(message: types.Message):
    chat_id = message.chat.id
    word = random.choice(GAME_WORDS)
    
    await message.answer(
        f"🎮 **КРОКОДИЛ**\nВедущий: {message.from_user.full_name}",
        reply_markup=get_game_keyboard(chat_id)
    )
    
    blank = base64.b64decode(BLANK_PNG_B64)
    # Посылаем ФОТО, чтобы можно было редактировать медиа
    prev = await message.answer_photo(
        photo=BufferedInputFile(blank, "b.png"), 
        caption="⏳ *Запуск...*", 
        parse_mode="Markdown"
    )

    game_sessions[str(chat_id)] = {
        "word": word,
        "drawer_id": message.from_user.id,
        "drawer_name": message.from_user.full_name,
        "preview_message_id": prev.message_id,
        "last_preview_time": 0
    }

async def handle_callback(cb: types.CallbackQuery):
    data = cb.data
    chat_id = data.split("_")[-1]
    session = game_sessions.get(chat_id)
    
    if not session: return await cb.answer("Игра не найдена")

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
    if not sess or not msg.text: return False
    
    if msg.text.strip().lower() == sess["word"]:
        if msg.from_user.id == sess["drawer_id"]: return True
        await msg.answer(f"🎉 **{msg.from_user.full_name}** победил! Слово: **{sess['word']}**")
        if sess.get("preview_message_id"):
            try: await bot.delete_message(msg.chat.id, sess["preview_message_id"])
            except: pass
        game_sessions.pop(cid, None)
        return True
    return False
