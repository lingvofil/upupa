# crocodile.py
import random
import logging
import socketio
import asyncio
from aiohttp import web
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from config import model  # Используем твою модель из config

# ================== НАСТРОЙКИ (ВНУТРИ МОДУЛЯ) ==================
# Твой домен DuckDNS (обязательно HTTPS для Telegram)
WEB_APP_BASE_URL = "https://upupaepops.duckdns.org" 
WEB_APP_PATH = "/game"
WEBAPP_URL = f"{WEB_APP_BASE_URL}{WEB_APP_PATH}"

# Порт, на котором слушает сокет-сервер внутри сервера
SOCKET_SERVER_PORT = 8080

# Состояние игры: {chat_id: {"word": "слово", "drawer_id": 123}}
game_sessions = {}

# ================== ЧАСТЬ 1: WebSocket и HTTP Сервер ==================

sio = socketio.AsyncServer(async_mode='aiohttp', cors_allowed_origins='*')
app_game = web.Application()
sio.attach(app_game)

@sio.event
async def join_room(sid, data):
    room = str(data.get('room'))
    sio.enter_room(sid, room)
    logging.info(f"Socket {sid} joined room {room}")

@sio.event
async def draw_step(sid, data):
    # Трансляция координат всем участникам в комнате чата
    await sio.emit('draw_data', data, room=str(data.get('room')), skip_sid=sid)

@sio.event
async def clear_canvas(sid, data):
    await sio.emit('clear', {}, room=str(data.get('room')), skip_sid=sid)

async def serve_index(request):
    """Отдает HTML-файл фронтенда"""
    try:
        return web.FileResponse('index.html')
    except Exception as e:
        logging.error(f"Error serving index.html: {e}")
        return web.Response(text="Файл index.html не найден в корне бота", status=404)

# Настройка маршрута для Mini App
app_game.router.add_get(WEB_APP_PATH, serve_index)

async def start_socket_server():
    """Запуск сервера на указанном порту"""
    runner = web.AppRunner(app_game)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"=== Crocodile Game Server started on port {SOCKET_SERVER_PORT} ===")


# ================== ЧАСТЬ 2: Логика игры ==================

async def generate_game_word():
    """Генерация слова через Gemini 2.0"""
    prompt = (
        "Ты ведущий игры Крокодил. Придумай ОДНО существительное на русском языке, "
        "которое интересно рисовать. Ответь только этим словом, без знаков препинания."
    )
    try:
        response = await model.generate_content(prompt)
        word = response.text.strip().lower().replace(".", "").split()[0]
        return word
    except Exception as e:
        logging.error(f"Gemini error: {e}")
        # Запасной список на случай ошибки API
        return random.choice(["космонавт", "шаурма", "синхрофазотрон", "кактус", "программист"])

def get_game_keyboard(chat_id):
    """Создает клавиатуру с кнопкой запуска Mini App"""
    # Добавляем chat_id в параметры, чтобы фронтенд знал комнату
    url = f"{WEBAPP_URL}?chat_id={chat_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Открыть холст", web_app=WebAppInfo(url=url))]
    ])

async def is_correct_answer(chat_id, text):
    """Проверка правильности ответа"""
    chat_id_str = str(chat_id)
    if chat_id_str in game_sessions:
        target_word = game_sessions[chat_id_str]['word']
        if text.strip().lower() == target_word:
            return True
    return False
