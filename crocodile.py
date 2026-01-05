# crocodile.py
import random
import logging
import socketio
from aiohttp import web
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from config import WEBAPP_URL, model

# ================== ЧАСТЬ 1: WebSocket и Сервер ==================

sio = socketio.AsyncServer(async_mode='aiohttp', cors_allowed_origins='*')
app_socket = web.Application()
sio.attach(app_socket)

@sio.event
async def join_room(sid, data):
    room = str(data.get('room'))
    sio.enter_room(sid, room)
    logging.info(f"Socket {sid} joined room {room}")

@sio.event
async def draw_step(sid, data):
    # Трансляция координат рисования всем в комнате (чате)
    await sio.emit('draw_data', data, room=str(data.get('room')), skip_sid=sid)

@sio.event
async def clear_canvas(sid, data):
    # Очистка холста у всех участников
    await sio.emit('clear', {}, room=str(data.get('room')), skip_sid=sid)

async def serve_index(request):
    """Раздача HTML-файла игры"""
    try:
        return web.FileResponse('index.html')
    except Exception as e:
        logging.error(f"Error serving index.html: {e}")
        return web.Response(text="Файл игры не найден", status=404)

# Маршрут для загрузки интерфейса игры
app_socket.router.add_get('/game', serve_index)

async def start_socket_server():
    """Запуск сервера на отдельном порту (8080)"""
    runner = web.AppRunner(app_socket)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logging.info("Crocodile Game Server started on port 8080")


# ================== ЧАСТЬ 2: Бизнес-логика ==================

async def generate_game_word():
    """Генерирует слово для игры через Gemini 2.0 Flash"""
    prompt = (
        "Ты — ведущий игры 'Крокодил'. Придумай ОДНО забавное или необычное существительное, "
        "которое можно нарисовать. Ответь только этим словом, без лишних знаков."
    )
    try:
        response = await model.generate_content(prompt)
        word = response.text.strip().split()[0]
        return word
    except Exception as e:
        logging.error(f"Word generation error: {e}")
        return random.choice(["Синхрофазотрон", "Оливье", "Чебурашка", "Гравитация"])

def get_game_keyboard(chat_id):
    """Создает кнопку открытия Mini App"""
    # WEBAPP_URL берется из Config.py (например, https://твой-домен.com/game)
    url = f"{WEBAPP_URL}?chat_id={chat_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Рисовать на холсте", web_app=WebAppInfo(url=url))]
    ])
