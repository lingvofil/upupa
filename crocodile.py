#crocodile.py

import os
import random
import logging
import socketio
import asyncio
from aiohttp import web
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from config import model  # Твоя модель

# ================== НАСТРОЙКИ (ВНУТРИ МОДУЛЯ) ==================
# Жестко прописываем домен на основе данных твоего личного кабинета DuckDNS
WEB_APP_DOMAIN = "upupaepops.duckdns.org"
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

@sio.event
async def draw_step(sid, data):
    await sio.emit('draw_data', data, room=str(data.get('room')), skip_sid=sid)

@sio.event
async def clear_canvas(sid, data):
    await sio.emit('clear', {}, room=str(data.get('room')), skip_sid=sid)

async def serve_index(request):
    """Раздача фронтенда с использованием абсолютного пути"""
    try:
        # Определяем путь к index.html относительно текущего файла crocodile.py
        current_dir = os.path.dirname(os.path.abspath(__file__))
        file_path = os.path.join(current_dir, 'index.html')
        
        if os.path.exists(file_path):
            return web.FileResponse(file_path)
        else:
            logging.error(f"File not found: {file_path}")
            return web.Response(text="index.html не найден в директории бота", status=404)
    except Exception as e:
        logging.error(f"Error serving index.html: {e}")
        return web.Response(text="Ошибка сервера при загрузке игры", status=500)

app_game.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app_game)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', SOCKET_SERVER_PORT)
    await site.start()
    logging.info(f"=== Crocodile Server started on port {SOCKET_SERVER_PORT} ===")

# ================== ЧАСТЬ 2: Логика игры ==================

async def generate_game_word():
    """Генерация слова (совместимо с твоим ModelFallbackWrapper)"""
    prompt = "Придумай одно существительное на русском языке для игры Крокодил. Только одно слово без знаков препинания."
    try:
        # Используем thread для синхронного вызова обертки
        def sync_call():
            return model.generate_content(prompt)
            
        response = await asyncio.to_thread(sync_call)
        
        if response and hasattr(response, 'text'):
            word = response.text.strip().lower().split()[0]
            return word
        return random.choice(["трактор", "кактус", "пельмень"])
    except Exception as e:
        logging.error(f"Gemini error in generate_game_word: {e}")
        return random.choice(["бегемот", "телевизор", "колбаса"])

def get_game_keyboard(chat_id):
    """Создает клавиатуру с гарантированно чистым URL для Telegram"""
    # Превращаем ID чата в строку и чистим его для URL
    # Используем префикс 'm' для отрицательных ID, чтобы Telegram не браковал URL
    safe_chat_id = str(chat_id).replace("-", "m") 
    
    # Формируем URL. Параметр 'cid' должен обрабатываться в index.html
    clean_url = f"{WEB_APP_URL_BASE}?cid={safe_chat_id}".strip()
    
    logging.info(f"DEBUG: Создание кнопки с URL: '{clean_url}'")
    
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
        logging.error(f"Error creating InlineKeyboardMarkup: {e}")
        return None

async def is_correct_answer(chat_id, text):
    """Сверка ответа игрока с загаданным словом"""
    chat_id_str = str(chat_id)
    if chat_id_str in game_sessions and text:
        target_word = game_sessions[chat_id_str]['word']
        return text.strip().lower() == target_word.lower()
    return False
