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

# Интервал обновления превью в секундах
PREVIEW_UPDATE_INTERVAL = 3.0 

# ================== ХРАНИЛИЩЕ СОСТОЯНИЯ ==================
# game_sessions[chat_id] = { 
#    word, drawer_id, drawer_name, 
#    preview_message_id, last_preview_time 
# }
game_sessions: dict[str, dict] = {}

# ================== SOCKET.IO SERVER ==================
sio = socketio.AsyncServer(
    async_mode="aiohttp",
    cors_allowed_origins="*",
    max_http_buffer_size=10 * 1024 * 1024,
)

app = web.Application()
sio.attach(app)

@sio.event
async def join_room(sid, data):
    """Подключение Mini App к комнате чата"""
    room = str(data.get("room"))
    sio.enter_room(sid, room)
    logging.info(f"[socket] {sid} joined room {room}")

@sio.event
async def draw_step(sid, data):
    """Ретрансляция штриха другим игрокам"""
    room = str(data.get("room"))
    # skip_sid=sid чтобы не отправлять обратно рисующему
    await sio.emit("draw_data", data, room=room, skip_sid=sid)

@sio.event
async def preview_snapshot(sid, data):
    """
    Периодическое обновление картинки в чате (Live-трансляция)
    """
    room = str(data.get("room"))
    
    # Определяем chat_id (m123 -> -123)
    if room.startswith("m"):
        chat_id = int(room.replace("m", "-"))
    else:
        chat_id = int(room)
    
    str_chat_id = str(chat_id)
    session = game_sessions.get(str_chat_id)
    
    if not session:
        # logging.warning(f"Session not found for {str_chat_id}")
        return

    # Проверка на троттлинг
    now = time.time()
    last_update = session.get("last_preview_time", 0)
    if now - last_update < PREVIEW_UPDATE_INTERVAL:
        return

    msg_id = session.get("preview_message_id")
    if not msg_id:
        return

    try:
        # print(f"Processing snapshot for {chat_id}...") # Debug
        header, encoded = data["image"].split(",", 1)
        image_bytes = base64.b64decode(encoded)
        
        # Обновляем сообщение. Важно: используем InputMediaPhoto
        media = InputMediaPhoto(
            media=BufferedInputFile(image_bytes, filename="preview.jpg"),
            caption=f"🎨 **LIVE:** {session['drawer_name']} рисует..."
        )
        
        await bot.edit_message_media(
            media=media,
            chat_id=chat_id,
            message_id=msg_id
        )
        
        session["last_preview_time"] = now

    except Exception as e:
        error_str = str(e)
        # Игнорируем ошибку, если картинка не изменилась
        if "message is not modified" not in error_str.lower():
            logging.warning(f"[socket] preview update failed: {e}")

@sio.event
async def skip_turn(sid, data):
    """Запрос на смену слова из Web App"""
    # Этот хендлер можно оставить для WebApp кнопки, если она есть,
    # или переиспользовать логику в handle_callback для Telegram-кнопки
    await handle_skip_logic(data.get("room"), sid)

async def handle_skip_logic(room: str, sid=None):
    if room.startswith("m"):
        chat_id = int(room.replace("m", "-"))
    else:
        chat_id = int(room)
        
    session = game_sessions.get(str(chat_id))
    if not session:
        return

    new_word = await generate_game_word()
    session["word"] = new_word
    
    # Уведомляем всех в комнате (или только ведущего), что слово изменилось
    # Лучше всех, чтобы очистился холст у всех наблюдателей тоже
    await sio.emit("new_word_data", {"word": new_word}, room=room)

@sio.event
async def final_frame(sid, data):
    """Приём финального изображения"""
    room = str(data.get("room"))
    if room.startswith("m"):
        chat_id = int(room.replace("m", "-"))
    else:
        chat_id = int(room)

    session = game_sessions.get(str(chat_id))
    if not session:
        return

    try:
        header, encoded = data["image"].split(",", 1)
        image_bytes = base64.b64decode(encoded)
        
        # Удаляем превью сообщение
        if session.get("preview_message_id"):
            try:
                await bot.delete_message(chat_id, session["preview_message_id"])
            except:
                pass

        await bot.send_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(image_bytes, filename="result.jpg"),
            caption=(
                f"🏁 **Раунд окончен!**\n"
                f"Слово было: **{session['word']}**"
            ),
        )
    except Exception as e:
        logging.exception(f"[socket] final_frame error: {e}")
    finally:
        game_sessions.pop(str(chat_id), None)


# ================== WEB SERVER ==================

async def serve_index(request: web.Request):
    return web.FileResponse("index.html")

app.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SOCKET_SERVER_HOST, SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"[socket] server started on {SOCKET_SERVER_HOST}:{SOCKET_SERVER_PORT}")


# ================== GAME LOGIC ==================

async def generate_game_word() -> str:
    try:
        def sync_call():
            return model.generate_content(
                "Придумай одно простое существительное для игры Крокодил на русском языке. Только слово."
            )
        response = await asyncio.to_thread(sync_call)
        word = response.text.strip().lower().split()[0]
        clean_word = "".join(filter(str.isalpha, word))
        return clean_word if clean_word else "яблоко"
    except Exception:
        return random.choice(["трактор", "кактус", "пельмень", "бегемот", "солнце", "жираф"])

def get_game_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """
    Кнопки в чате:
    1. Открыть холст (ссылка)
    2. Показать слово | Следующее слово (callback)
    """
    safe_chat_id = str(chat_id).replace("-", "m")
    app_link = f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}?startapp={safe_chat_id}"
    
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Открыть холст", url=app_link)],
            [
                InlineKeyboardButton(text="👁 Показать слово", callback_data=f"cr_w_{chat_id}"),
                InlineKeyboardButton(text="🔄 Следующее слово", callback_data=f"cr_n_{chat_id}")
            ]
        ]
    )

async def handle_start_game(message: types.Message):
    """Старт новой игры"""
    chat_id = message.chat.id
    word = await generate_game_word()
    
    start_msg = await message.answer(
        f"🎮 **КРОКОДИЛ НАЧАТ!**\n"
        f"Ведущий: {message.from_user.full_name}\n"
        f"Загадывающий, нажми 'Открыть холст'!",
        reply_markup=get_game_keyboard(chat_id),
    )
    
    # Сообщение-заглушка для трансляции
    preview_msg = await message.answer("⏳ *Ожидание первого штриха...*", parse_mode="Markdown")

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
        return await callback.answer("Игра окончена")

    # Проверка, что нажимает ведущий
    if callback.from_user.id != session["drawer_id"]:
        return await callback.answer("Только ведущий может управлять!", show_alert=True)

    # Показать слово
    if data.startswith("cr_w_"):
        await callback.answer(f"СЛОВО: {session['word'].upper()}", show_alert=True)

    # Следующее слово
    elif data.startswith("cr_n_"):
        # Генерируем новое
        new_word = await generate_game_word()
        session["word"] = new_word
        
        # Уведомляем ведущего тут
        await callback.answer(f"Новое слово: {new_word.upper()}", show_alert=True)
        
        # Уведомляем WebApp (чтобы очистился холст и показался алерт внутри)
        # Формируем room_id как m(chat_id) или просто chat_id
        # В сессии ключ - это str(chat_id) (напр "-100...")
        # WebApp использует "m100..."
        safe_room = f"m{chat_id.replace('-', '')}" if chat_id.startswith("-") else chat_id
        
        await sio.emit("new_word_data", {"word": new_word}, room=safe_room)


async def check_answer(message: types.Message) -> bool:
    chat_id = str(message.chat.id)
    session = game_sessions.get(chat_id)

    if not session or not message.text:
        return False

    if message.text.strip().lower() == session["word"]:
        if message.from_user.id == session["drawer_id"]:
            return True 

        winner_name = message.from_user.full_name
        word = session['word']
        
        await message.answer(f"🎉 **{winner_name}** угадал слово: **{word.upper()}**!")
        
        if session.get("preview_message_id"):
            try:
                await bot.delete_message(message.chat.id, session["preview_message_id"])
            except: 
                pass
        
        game_sessions.pop(chat_id, None)
        return True

    return False
