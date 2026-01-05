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
WEBAPP_URL = f"https://{WEB_APP_DOMAIN}/game"

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

app_game.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app_game)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"Crocodile Server started on {SOCKET_SERVER_PORT}")

# ================== ЧАСТЬ 2: Логика игры ==================

async def generate_game_word():
    """Генерация слова (совместимая с твоим ModelFallbackWrapper)"""
    prompt = "Придумай одно существительное на русском языке для игры Крокодил. Только одно слово без знаков препинания."
    try:
        # Так как твоя обертка не поддерживает async, используем thread для запуска синхронного метода
        # Это не даст боту зависнуть во время генерации
        def call_gemini():
            return model.generate_content(prompt)
            
        response = await asyncio.to_thread(call_gemini)
        
        # Безопасно достаем текст
        if hasattr(response, 'text') and response.text:
            word = response.text.strip().lower().split()[0]
            return word
        return random.choice(["трактор", "кактус", "пельмень"])
    except Exception as e:
        logging.error(f"Gemini error in generate_game_word: {e}")
        return random.choice(["бегемот", "телевизор", "колбаса"])

def get_game_keyboard(chat_id):
    """Создает клавиатуру с очисткой URL"""
    # Telegram очень чувствителен к формату. Очищаем всё лишнее.
    clean_url = f"{WEBAPP_URL}?chat_id={chat_id}".strip().replace(" ", "")
    
    # Логируем URL для отладки, если кнопка снова упадет
    logging.info(f"Generated WebApp URL: {clean_url}")
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎨 Открыть холст", 
            web_app=WebAppInfo(url=clean_url)
        )]
    ])

async def is_correct_answer(chat_id, text):
    chat_id_str = str(chat_id)
    if chat_id_str in game_sessions and text:
        target_word = game_sessions[chat_id_str]['word']
        return text.strip().lower() == target_word.lower()
    return False
