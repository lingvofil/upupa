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

# Интервал обновления превью в секундах (чтобы не словить FloodWait от Telegram)
PREVIEW_UPDATE_INTERVAL = 4.0 

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
    """Ретрансляция штриха другим игрокам (если они тоже открыли Mini App)"""
    room = str(data.get("room"))
    await sio.emit("draw_data", data, room=room, skip_sid=sid)

@sio.event
async def preview_snapshot(sid, data):
    """
    Периодическое обновление картинки в чате (Live-трансляция)
    """
    room = str(data.get("room"))
    
    # Определяем chat_id
    if room.startswith("m"):
        chat_id = int(room.replace("m", "-"))
    else:
        chat_id = int(room)

    session = game_sessions.get(str(chat_id))
    if not session:
        return

    # Проверка на троттлинг (не чаще чем раз в N секунд)
    now = time.time()
    last_update = session.get("last_preview_time", 0)
    if now - last_update < PREVIEW_UPDATE_INTERVAL:
        return

    msg_id = session.get("preview_message_id")
    if not msg_id:
        return

    try:
        # Декодируем картинку
        header, encoded = data["image"].split(",", 1)
        image_bytes = base64.b64decode(encoded)
        
        # Обновляем сообщение в чате
        # Используем edit_message_media для обновления картинки без удаления
        media = InputMediaPhoto(
            media=BufferedInputFile(image_bytes, filename="preview.jpg"),
            caption=f"🎨 **LIVE:** {session['drawer_name']} рисует..."
        )
        
        await bot.edit_message_media(
            media=media,
            chat_id=chat_id,
            message_id=msg_id
        )
        
        # Обновляем время последнего апдейта
        session["last_preview_time"] = now

    except Exception as e:
        # Часто бывает, что картинка не изменилась (Telegram не дает редактировать на то же самое)
        # или сеть лагает. Игнорируем мелкие ошибки.
        logging.warning(f"[socket] preview update failed: {e}")

@sio.event
async def skip_turn(sid, data):
    """
    Запрос на смену слова от ведущего
    """
    room = str(data.get("room"))
    if room.startswith("m"):
        chat_id = int(room.replace("m", "-"))
    else:
        chat_id = int(room)
        
    session = game_sessions.get(str(chat_id))
    if not session:
        return

    # Генерируем новое слово
    new_word = await generate_game_word()
    session["word"] = new_word
    
    logging.info(f"Word skipped. New word for chat {chat_id}: {new_word}")

    # Отправляем новое слово ТОЛЬКО ведущему (sid)
    await sio.emit("new_word_data", {"word": new_word}, to=sid)


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
        
        # Если было сообщение с превью, удаляем его, чтобы не захламлять,
        # или редактируем его в финальное (по желанию). 
        # Здесь удалим старое превью и отправим новое чистое сообщение.
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
    """Генерация слова"""
    try:
        # Вызов модели (предполагаем, что model настроен в config)
        def sync_call():
            return model.generate_content(
                "Придумай одно простое существительное для игры Крокодил на русском языке. Только слово."
            )
        response = await asyncio.to_thread(sync_call)
        word = response.text.strip().lower().split()[0]
        clean_word = "".join(filter(str.isalpha, word))
        return clean_word if clean_word else "яблоко"
    except Exception as e:
        logging.error(f"Error generating word: {e}")
        return random.choice(["трактор", "кактус", "пельмень", "бегемот", "солнце", "жираф"])

def get_game_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    safe_chat_id = str(chat_id).replace("-", "m")
    app_link = f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}?startapp={safe_chat_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Рисовать / Смотреть", url=app_link)],
            [InlineKeyboardButton(text="👁 Напомнить слово", callback_data=f"cr_w_{chat_id}")],
        ]
    )

async def handle_start_game(message: types.Message):
    """Старт новой игры"""
    chat_id = message.chat.id
    word = await generate_game_word()
    
    # 1. Отправляем стартовое сообщение
    start_msg = await message.answer(
        f"🎮 **КРОКОДИЛ НАЧАТ!**\n"
        f"Ведущий: {message.from_user.full_name}\n"
        f"Ждем рисунка...",
        reply_markup=get_game_keyboard(chat_id),
    )
    
    # 2. Сразу создаем "Плейсхолдер" для трансляции
    # Мы отправим заглушку, которую будем редактировать через сокеты
    preview_msg = await message.answer("⏳ *Ожидание первого штриха...*", parse_mode="Markdown")

    game_sessions[str(chat_id)] = {
        "word": word,
        "drawer_id": message.from_user.id,
        "drawer_name": message.from_user.full_name,
        "preview_message_id": preview_msg.message_id, # ID для лайв-трансляции
        "last_preview_time": 0
    }

async def handle_callback(callback: types.CallbackQuery):
    data = callback.data
    chat_id = data.split("_")[-1]
    session = game_sessions.get(chat_id)

    if not session:
        return await callback.answer("Игра окончена")

    if data.startswith("cr_w_"):
        if callback.from_user.id == session["drawer_id"]:
             await callback.answer(f"СЛОВО: {session['word'].upper()}", show_alert=True)
        else:
             await callback.answer("Подглядывать нехорошо! 😡", show_alert=True)

async def check_answer(message: types.Message) -> bool:
    chat_id = str(message.chat.id)
    session = game_sessions.get(chat_id)

    if not session or not message.text:
        return False

    if message.text.strip().lower() == session["word"]:
        if message.from_user.id == session["drawer_id"]:
            return True # Ведущий пишет слово - игнорим

        # Победитель
        winner_name = message.from_user.full_name
        word = session['word']
        
        await message.answer(
            f"🎉 **{winner_name}** угадал слово: **{word.upper()}**!"
        )
        
        # Можно тут же удалить сессию или ждать финала от ведущего.
        # Обычно лучше ждать, пока ведущий нажмет "Завершить", 
        # или принудительно завершать тут. 
        # Для простоты - завершим сессию здесь и сообщим сокетам.
        
        # Опционально: отправить сигнал в Mini App, что игра окончена
        # await sio.emit("game_over", {"winner": winner_name}, room=room_id)
        
        # Удаляем превью
        if session.get("preview_message_id"):
            try:
                await bot.delete_message(message.chat.id, session["preview_message_id"])
            except: 
                pass
                
        game_sessions.pop(chat_id, None)
        return True

    return False
