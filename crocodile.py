#crocodile.py

import os
import random
import logging
import socketio
import asyncio
import urllib.parse
from aiohttp import web
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from config import model  # Ваша модель

# ================== НАСТРОЙКИ ==================
# Временный домен от Cloudflare (БЕЗ https:// и лишних слэшей)
WEB_APP_DOMAIN = "invitations-adjusted-eggs-banana.trycloudflare.com"
WEB_APP_URL_BASE = f"https://{WEB_APP_DOMAIN}/game"

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
    logging.info(f"Socket: User {sid} joined room {room}")

@sio.event
async def draw_step(sid, data):
    await sio.emit('draw_data', data, room=str(data.get('room')), skip_sid=sid)

@sio.event
async def clear_canvas(sid, data):
    await sio.emit('clear', {}, room=str(data.get('room')), skip_sid=sid)

async def serve_index(request):
    """Раздача фронтенда из папки /var/www/crocodile"""
    try:
        file_path = '/var/www/crocodile/index.html'
        if os.path.exists(file_path):
            return web.FileResponse(file_path)
        else:
            logging.error(f"File not found: {file_path}")
            return web.Response(text="index.html не найден в /var/www/crocodile", status=404)
    except Exception as e:
        logging.error(f"Error serving index.html: {e}")
        return web.Response(text="Ошибка сервера при загрузке игры", status=500)

app_game.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app_game)
    await runner.setup()
    # Слушаем 127.0.0.1, так как Nginx стоит перед нами
    site = web.TCPSite(runner, '127.0.0.1', SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"=== Crocodile Socket Server started on 127.0.0.1:{SOCKET_SERVER_PORT} ===")

# ================== ЧАСТЬ 2: Логика игры ==================

async def generate_game_word():
    """Генерация слова через Gemini или Fallback"""
    prompt = "Придумай одно существительное на русском языке для игры Крокодил. Только одно слово без знаков препинания."
    try:
        def sync_call():
            return model.generate_content(prompt)
        response = await asyncio.to_thread(sync_call)
        if response and hasattr(response, 'text'):
            word = response.text.strip().lower().split()[0]
            # Убираем возможную пунктуацию в конце
            word = "".join(filter(str.isalpha, word))
            return word
        return random.choice(["трактор", "кактус", "пельмень"])
    except Exception as e:
        logging.error(f"Gemini error in generate_game_word: {e}")
        return random.choice(["бегемот", "телевизор", "колбаса"])

def get_game_keyboard(chat_id):
    """Создает клавиатуру с максимально чистым URL"""
    safe_chat_id = str(chat_id).replace("-", "m").strip()
    
    # Формируем параметры URL через urllib для безопасности
    params = urllib.parse.urlencode({'cid': safe_chat_id})
    # Убеждаемся, что база не имеет лишних слэшей
    clean_url = f"{WEB_APP_URL_BASE}?{params}".replace(" ", "")
    
    # Отладочный вывод в консоль
    print(f"--- ATTEMPTING TO SEND URL: {clean_url} ---")
    
    try:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎨 Открыть холст", 
                        web_app=WebAppInfo(url=clean_url)
                    )
                ]
            ]
        )
    except Exception as e:
        logging.error(f"Failed to create InlineKeyboardMarkup: {e}")
        return None

async def is_correct_answer(chat_id, text):
    """Проверка ответа"""
    chat_id_str = str(chat_id)
    if chat_id_str in game_sessions and text:
        target_word = game_sessions[chat_id_str]['word']
        return text.strip().lower() == target_word.lower()
    return False
