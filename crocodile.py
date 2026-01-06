import asyncio
import base64
import logging
import random
from aiohttp import web
import socketio
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from config import bot, model

# ================== НАСТРОЙКИ ==================
BOT_USERNAME = "expertyebaniebot"
WEB_APP_SHORT_NAME = "upupadile"
SOCKET_SERVER_HOST = "127.0.0.1"
SOCKET_SERVER_PORT = 8080

# ================== ХРАНИЛИЩЕ СОСТОЯНИЯ ==================
# game_sessions[chat_id] = { word, drawer_id }
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
    """Ретрансляция одного штриха всем участникам комнаты, кроме отправителя"""
    room = str(data.get("room"))
    await sio.emit(
        "draw_data",
        data,
        room=room,
        skip_sid=sid,
    )


@sio.event
async def final_frame(sid, data):
    """
    Приём финального изображения от ведущего и отправка его в Telegram-чат
    ОДИН РАЗ в конце раунда
    """
    room = str(data.get("room"))
    if room.startswith("m"):
        chat_id = int(room.replace("m", "-"))
    else:
        chat_id = int(room)

    session = game_sessions.get(str(chat_id))
    if not session:
        logging.warning(f"[socket] final_frame: session {chat_id} not found")
        return

    try:
        header, encoded = data["image"].split(",", 1)
        image_bytes = base64.b64decode(encoded)

        await bot.send_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(image_bytes, filename="crocodile_result.jpg"),
            caption=(
                f"🎨 Финальный рисунок\n"
                f"Слово: **{session['word']}**"
            ),
        )
    except Exception as e:
        logging.exception(f"[socket] final_frame error: {e}")
    finally:
        game_sessions.pop(str(chat_id), None)


# ================== WEB SERVER (MINI APP) ==================

async def serve_index(request: web.Request):
    """Отдаёт index.html Mini App"""
    return web.FileResponse("index.html")


app.router.add_get("/game", serve_index)


async def start_socket_server():
    """Запуск Socket.IO сервера"""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SOCKET_SERVER_HOST, SOCKET_SERVER_PORT)
    await site.start()
    logging.info(
        f"[socket] server started on {SOCKET_SERVER_HOST}:{SOCKET_SERVER_PORT}"
    )


# ================== GAME LOGIC (BOT) ==================

async def generate_game_word() -> str:
    """Генерация слова для игры"""
    try:
        def sync_call():
            return model.generate_content(
                "Придумай одно существительное для игры Крокодил"
            )

        response = await asyncio.to_thread(sync_call)
        word = response.text.strip().lower().split()[0]
        return "".join(filter(str.isalpha, word))
    except Exception:
        return random.choice(["трактор", "кактус", "пельмень", "бегемот"])



def get_game_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """Клавиатура запуска Mini App"""
    safe_chat_id = str(chat_id).replace("-", "m")
    app_link = (
        f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}"
        f"?startapp={safe_chat_id}"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Открыть холст", url=app_link)],
            [InlineKeyboardButton(text="👁 Показать слово", callback_data=f"cr_w_{chat_id}")],
        ]
    )


async def handle_start_game(message: types.Message):
    """Старт новой игры"""
    chat_id = message.chat.id
    word = await generate_game_word()

    game_sessions[str(chat_id)] = {
        "word": word,
        "drawer_id": message.from_user.id,
        "drawer_name": message.from_user.full_name,
    }

    await message.answer(
        f"🎮 **КРОКОДИЛ НАЧАТ!**\n"
        f"Ведущий: {message.from_user.full_name}",
        reply_markup=get_game_keyboard(chat_id),
    )


async def handle_callback(callback: types.CallbackQuery):
    """Обработка inline-кнопок"""
    data = callback.data
    chat_id = data.split("_")[-1]
    session = game_sessions.get(chat_id)

    if not session:
        return await callback.answer("Игра окончена")

    if callback.from_user.id != session["drawer_id"]:
        return await callback.answer("Только ведущий")

    if data.startswith("cr_w_"):
        await callback.answer(
            f"СЛОВО: {session['word'].upper()}", show_alert=True
        )


async def check_answer(message: types.Message) -> bool:
    """Проверка ответа игрока"""
    chat_id = str(message.chat.id)
    session = game_sessions.get(chat_id)

    if not session or not message.text:
        return False

    if message.text.strip().lower() == session["word"]:
        if message.from_user.id == session["drawer_id"]:
            return True

        await message.answer(
            f"🎉 **{message.from_user.full_name}** угадал слово:"
            f" **{session['word']}**"
        )

        # Ждём финальный кадр из Mini App
        return True

    return False
