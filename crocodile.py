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

# Статичный список слов (надежно и бесплатно)
GAME_WORDS = [
    "трактор", "арбуз", "банан", "жираф", "солнце", "дом", "дерево", "машина", "телефон", "книга",
    "часы", "яблоко", "кошка", "собака", "рыба", "птица", "самолет", "лодка", "велосипед", "мяч",
    "кукла", "робот", "звезда", "луна", "облако", "дождь", "снег", "зонт", "шляпа", "очки",
    "усы", "борода", "волосы", "глаз", "нос", "рот", "ухо", "рука", "нога", "сердце",
    "цветок", "трава", "лес", "гора", "река", "море", "океан", "остров", "пляж", "песок",
    "камень", "огонь", "дым", "ветер", "молния", "гром", "радуга", "свет", "тень", "ночь"
]

game_sessions: dict[str, dict] = {}

# Увеличиваем ping_timeout, так как polling через туннели может лагать
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
async def join_room(sid, data):
    room = str(data.get("room"))
    sio.enter_room(sid, room)
    logging.info(f"[socket] {sid} joined {room}")

@sio.on('*')
async def catch_all(event, sid, data):
    """Ловим ВСЕ события для отладки (кроме служебных)"""
    if event not in ['draw_step', 'join_room']:
        # Если пришло preview_snapshot, покажем размер
        if event == 'preview_snapshot':
            size = len(data.get('image', '')) if isinstance(data, dict) else 0
            logging.info(f"[DEBUG] INCOMING EVENT: {event} (size: {size} bytes)")
        else:
            logging.info(f"[DEBUG] INCOMING EVENT: {event}")

@sio.event
async def draw_step(sid, data):
    room = str(data.get("room"))
    await sio.emit("draw_data", data, room=room, skip_sid=sid)

@sio.event
async def preview_snapshot(sid, data):
    try:
        room = str(data.get("room"))
        chat_id = get_chat_id_from_room(room)
        session = game_sessions.get(chat_id)

        if not session:
            logging.warning(f"Session not found for room {room}")
            return

        # Троттлинг
        now = time.time()
        if now - session.get("last_preview_time", 0) < PREVIEW_UPDATE_INTERVAL:
            return

        msg_id = session.get("preview_message_id")
        if not msg_id:
            return

        img_str = data.get("image", "")
        if not img_str: return

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
        logging.info(f"[SUCCESS] Preview updated for {chat_id}")

    except Exception as e:
        if "message is not modified" not in str(e).lower():
            logging.error(f"Preview fail: {e}")

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
            caption=f"🏁 **Стоп игра!**\nСлово было: **{session['word']}**"
        )
    except Exception as e:
        logging.error(f"Final error: {e}")
    finally:
        game_sessions.pop(chat_id, None)

# ================== SERVER & LOGIC ==================
async def serve_index(request: web.Request):
    return web.FileResponse("index.html")

app.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SOCKET_SERVER_HOST, SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"Socket running: {SOCKET_SERVER_HOST}:{SOCKET_SERVER_PORT}")

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
    prev = await message.answer_photo(BufferedInputFile(blank, "b.png"), caption="⏳ *Загрузка...*", parse_mode="Markdown")

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
    if not session: return await cb.answer("Игра окончена")

    if cb.from_user.id != session["drawer_id"]:
        return await cb.answer("Только ведущий!", show_alert=True)

    if data.startswith("cr_w_"):
        await cb.answer(f"Слово: {session['word'].upper()}", show_alert=True)
    elif data.startswith("cr_n_"):
        new_w = random.choice(GAME_WORDS)
        session["word"] = new_w
        await cb.answer(f"Новое: {new_w.upper()}", show_alert=True)
        room = f"m{chat_id.replace('-', '')}" if chat_id.startswith("-") else chat_id
        await sio.emit("new_word_data", {"word": new_w}, room=room)

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
