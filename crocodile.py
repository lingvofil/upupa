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
# ВАЖНО: Домен должен точно совпадать с тем, что в BotFather!
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
    # Рассылаем данные всем в комнате, кроме отправителя
    await sio.emit('draw_data', data, room=str(data.get('room')), skip_sid=sid)

@sio.event
async def clear_canvas(sid, data):
    await sio.emit('clear', {}, room=str(data.get('room')), skip_sid=sid)

async def serve_index(request):
    """Раздача index.html из той же папки, где лежит модуль"""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(current_dir, 'index.html')
        
        if os.path.exists(file_path):
            return web.FileResponse(file_path)
        else:
            logging.error(f"File not found: {file_path}")
            return web.Response(text="index.html не найден в папке проекта", status=404)
    except Exception as e:
        return web.Response(text=f"Ошибка сервера: {e}", status=500)

app_game.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app_game)
    await runner.setup()
    # Nginx проксирует на 127.0.0.1
    site = web.TCPSite(runner, '127.0.0.1', SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"=== Crocodile Socket Server started on 8080 ===")

# ================== ЧАСТЬ 2: Логика игры ==================

async def generate_game_word():
    prompt = "Придумай одно существительное на русском языке для игры Крокодил. Только одно слово без знаков препинания."
    try:
        def sync_call():
            return model.generate_content(prompt)
        response = await asyncio.to_thread(sync_call)
        if response and hasattr(response, 'text'):
            word = response.text.strip().lower().split()[0]
            word = "".join(filter(str.isalpha, word)) # Только буквы
            return word
        return random.choice(["трактор", "кактус", "пельмень"])
    except Exception as e:
        logging.error(f"Gemini error: {e}")
        return random.choice(["бегемот", "телевизор", "колбаса"])

def get_game_keyboard(chat_id):
    """
    Создает клавиатуру. 
    Если BUTTON_TYPE_INVALID не исчезнет, попробуйте изменить full_url 
    на чистый WEB_APP_URL_BASE (без ?cid=...)
    """
    safe_cid = str(chat_id).replace("-", "m").strip()
    
    # Формируем URL максимально чисто
    query = urllib.parse.urlencode({'cid': safe_cid})
    full_url = WEB_APP_URL_BASE
    
    # Отладка в логи (посмотрите их после команды)
    logging.info(f"Final MiniApp URL: {full_url}")

    try:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎨 Открыть холст",
                    web_app=WebAppInfo(url=full_url)
                )
            ]
        ])
    except Exception as e:
        logging.error(f"Error in keyboard creation: {e}")
        return None

async def is_correct_answer(chat_id, text):
    chat_id_str = str(chat_id)
    if chat_id_str in game_sessions and text:
        target_word = game_sessions[chat_id_str]['word']
        return text.strip().lower() == target_word.lower()
    return False
