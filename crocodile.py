#crocodile.py
import os
import random
import logging
import socketio
import asyncio
import urllib.parse
from aiohttp import web
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from config import model 

# ================== НАСТРОЙКИ ==================
WEB_APP_DOMAIN = "invitations-adjusted-eggs-banana.trycloudflare.com"
WEB_APP_URL_BASE = f"https://{WEB_APP_DOMAIN}/game"

# Короткое имя вашего приложения из BotFather (short_name)
WEB_APP_SHORT_NAME = "upupadile" 
# Ссылка на бота (без @)
BOT_USERNAME = "expertyebaniebot"

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
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(current_dir, 'index.html')
        if os.path.exists(file_path):
            return web.FileResponse(file_path)
        return web.Response(text="index.html не найден", status=404)
    except Exception as e:
        return web.Response(text=f"Ошибка: {e}", status=500)

app_game.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app_game)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"=== Crocodile Server started on port {SOCKET_SERVER_PORT} ===")

# ================== ЧАСТЬ 2: Логика игры ==================

async def generate_game_word():
    prompt = "Придумай одно существительное на русском языке для игры Крокодил. Только одно слово без знаков препинания."
    try:
        def sync_call():
            return model.generate_content(prompt)
        response = await asyncio.to_thread(sync_call)
        if response and hasattr(response, 'text'):
            word = response.text.strip().lower().split()[0]
            return "".join(filter(str.isalpha, word))
        return random.choice(["трактор", "кактус", "пельмень"])
    except Exception:
        return random.choice(["бегемот", "телевизор", "колбаса"])

def get_game_keyboard(chat_id):
    """
    Используем прямую ссылку t.me. 
    Параметр startapp передает ID чата внутрь Mini App.
    Это гарантированно решает проблему BUTTON_TYPE_INVALID.
    """
    safe_cid = str(chat_id).replace("-", "m").strip()
    
    # Формируем прямую ссылку на Mini App
    # Пример: https://t.me/expertyebaniebot/upupadile?startapp=m1001707530786
    direct_link = f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}?startapp={safe_cid}"
    
    logging.info(f"Sending direct link: {direct_link}")

    try:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                # Используем обычный url вместо web_app
                InlineKeyboardButton(
                    text="🎨 Открыть холст",
                    url=direct_link
                )
            ]
        ])
    except Exception as e:
        logging.error(f"Kbd Error: {e}")
        return None

async def is_correct_answer(chat_id, text):
    chat_id_str = str(chat_id)
    if chat_id_str in game_sessions and text:
        target_word = game_sessions[chat_id_str]['word']
        return text.strip().lower() == target_word.lower()
    return False
