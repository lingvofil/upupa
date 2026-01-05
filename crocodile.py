# crocodile.py
import random
import logging
import socketio
import asyncio
from aiohttp import web
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from config import model  # Твоя модель

# ================== НАСТРОЙКИ ==================
# Прописываем URL максимально жестко, чтобы исключить ошибки Telegram
WEB_APP_DOMAIN = "upupaepops.duckdns.org"
WEB_APP_PATH = "/game"
# Собираем базовый URL без параметров
WEBAPP_BASE_URL = f"https://{WEB_APP_DOMAIN}{WEB_APP_PATH}"

SOCKET_SERVER_PORT = 8080
game_sessions = {}

# ================== ЧАСТЬ 1: WebSocket и HTTP Сервер ==================
sio = socketio.AsyncServer(async_mode='aiohttp', cors_allowed_origins='*')
app_game = web.Application()
sio.attach(app_game)

@sio.event
async def join_room(sid, data):
    room = str(data.get('room'))
    sio.enter_room(sid, room)

@sio.event
async def draw_step(sid, data):
    await sio.emit('draw_data', data, room=str(data.get('room')), skip_sid=sid)

@sio.event
async def clear_canvas(sid, data):
    await sio.emit('clear', {}, room=str(data.get('room')), skip_sid=sid)

async def serve_index(request):
    try:
        return web.FileResponse('index.html')
    except Exception:
        return web.Response(text="index.html not found", status=404)

app_game.router.add_get(WEB_APP_PATH, serve_index)

async def start_socket_server():
    runner = web.AppRunner(app_game)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"Crocodile Server started on {SOCKET_SERVER_PORT}")

# ================== ЧАСТЬ 2: Логика игры ==================

async def generate_game_word():
    """Генерация слова через Gemini (совместимо с ModelFallbackWrapper)"""
    prompt = "Придумай одно существительное на русском языке для игры Крокодил. Только одно слово без знаков препинания."
    try:
        # Используем thread для синхронного вызова, так как обертка не поддерживает async
        def call_model():
            return model.generate_content(prompt)
            
        response = await asyncio.to_thread(call_model)
        
        if hasattr(response, 'text') and response.text:
            word = response.text.strip().lower().split()[0]
            return word
        return random.choice(["трактор", "кактус", "пельмень"])
    except Exception as e:
        logging.error(f"Gemini error in generate_game_word: {e}")
        return random.choice(["бегемот", "телевизор", "колбаса"])

def get_game_keyboard(chat_id):
    """Создает клавиатуру с ультра-чистым URL"""
    # Превращаем ID чата в строку и убираем лишнее
    str_chat_id = str(chat_id).strip()
    # Формируем URL и чистим его от любых пробелов или переносов
    clean_url = f"{WEBAPP_BASE_URL}?chat_id={str_chat_id}".replace(" ", "").strip()
    
    # Лог для проверки в консоли сервера
    logging.info(f"DEBUG: Отправка WebApp URL: '{clean_url}'")
    
    # Создаем кнопку. Важно: только text и web_app
    button = InlineKeyboardButton(
        text="🎨 Открыть холст", 
        web_app=WebAppInfo(url=clean_url)
    )
    
    return InlineKeyboardMarkup(inline_keyboard=[[button]])

async def is_correct_answer(chat_id, text):
    chat_id_str = str(chat_id)
    if chat_id_str in game_sessions and text:
        target_word = game_sessions[chat_id_str]['word']
        return text.strip().lower() == target_word.lower()
    return False
