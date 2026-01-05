#crocodile.py
import os
import random
import logging
import socketio
import asyncio
import base64
import io
import urllib.parse
from aiohttp import web
from aiogram import types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from config import model, bot # Используем бота из конфига

# ================== НАСТРОЙКИ ==================
WEB_APP_DOMAIN = "invitations-adjusted-eggs-banana.trycloudflare.com"
WEB_APP_SHORT_NAME = "upupadile" 
BOT_USERNAME = "expertyebaniebot"

SOCKET_SERVER_PORT = 8080
game_sessions = {} # {chat_id: {word, drawer_id, last_msg_id, last_photo_id}}
scores = {}        # {user_id: {name, points}}

# ================== ЧАСТЬ 1: WebSocket и Скриншоты ==================
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

@sio.event
async def send_frame(sid, data):
    """Получение скриншота от рисующего и отправка в чат"""
    room_id = data.get('room')
    image_data = data.get('image') # base64
    
    if not room_id or not image_data: return
    
    # Декодируем base64 в байты
    try:
        header, encoded = image_data.split(",", 1)
        data_bytes = base64.b64decode(encoded)
        
        chat_id = room_id.replace("m", "-") if "m" in room_id else room_id
        session = game_sessions.get(chat_id)
        
        if session:
            # Отправляем фото в чат (новое, чтобы не спамить редактированием медиа, которое медленное)
            photo = BufferedInputFile(data_bytes, filename="drawing.png")
            msg = await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=f"🖌 **Рисует {session.get('drawer_name', 'Ведущий')}...**\nУгадывайте слово!",
                disable_notification=True
            )
            
            # Удаляем предыдущий скриншот, чтобы не засорять чат
            if session.get('last_photo_id'):
                try: await bot.delete_message(chat_id, session['last_photo_id'])
                except: pass
            
            session['last_photo_id'] = msg.message_id
    except Exception as e:
        logging.error(f"Error sending frame to TG: {e}")

async def serve_index(request):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, 'index.html')
    return web.FileResponse(file_path) if os.path.exists(file_path) else web.Response(status=404)

app_game.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app_game)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', SOCKET_SERVER_PORT).start()

# ================== ЧАСТЬ 2: Логика Игры ==================

async def generate_game_word():
    prompt = "Придумай одно существительное на русском языке для игры Крокодил. Только одно слово."
    try:
        def sync_call(): return model.generate_content(prompt)
        response = await asyncio.to_thread(sync_call)
        word = response.text.strip().lower().split()[0]
        return "".join(filter(str.isalpha, word)) or "кактус"
    except:
        return random.choice(["бегемот", "телевизор", "пельмень"])

def get_game_keyboard(chat_id):
    safe_cid = str(chat_id).replace("-", "m")
    # Прямая ссылка на Mini App
    app_link = f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}?startapp={safe_cid}"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Открыть холст", url=app_link)],
        [
            InlineKeyboardButton(text="👁 Словцо", callback_data=f"cr_w_{chat_id}"),
            InlineKeyboardButton(text="🔄 Другое", callback_data=f"cr_n_{chat_id}")
        ]
    ])

# ================== ЧАСТЬ 3: Хендлеры (вызываются из main.py) ==================

async def handle_start_game(message: types.Message):
    if message.chat.type == 'private':
        return await message.reply("Только в группах!")
    
    chat_id = str(message.chat.id)
    word = await generate_game_word()
    
    game_sessions[chat_id] = {
        "word": word,
        "drawer_id": message.from_user.id,
        "drawer_name": message.from_user.full_name,
        "last_photo_id": None
    }
    
    await message.answer(
        f"🎮 **КРОКОДИЛ НАЧАТ!**\n\nВедущий: {message.from_user.full_name}\n"
        "Жми кнопку ниже, чтобы начать рисовать. Остальные — угадывайте!",
        reply_markup=get_game_keyboard(chat_id)
    )

async def handle_callback(callback: types.CallbackQuery):
    chat_id = callback.data.split("_")[-1]
    session = game_sessions.get(chat_id)
    
    if not session:
        return await callback.answer("Игра не найдена.", show_alert=True)

    if callback.data.startswith("cr_w_"): # Глянуть слово
        if callback.from_user.id != session['drawer_id']:
            return await callback.answer("Это не твое слово, иди нахуй!", show_alert=True)
        await callback.answer(f"Твое слово: {session['word'].upper()}", show_alert=True)
        
    elif callback.data.startswith("cr_n_"): # Следующее слово
        if callback.from_user.id != session['drawer_id']:
            return await callback.answer("Только ведущий меняет слово!", show_alert=True)
        session['word'] = await generate_game_word()
        await callback.answer("Слово заменено!", show_alert=True)

async def check_answer(message: types.Message):
    chat_id = str(message.chat.id)
    session = game_sessions.get(chat_id)
    
    if not session or not message.text: return False
    
    text = message.text.strip().lower()
    
    if text == session['word']:
        if message.from_user.id == session['drawer_id']:
            await message.reply("Ведущий, не подсказывай!")
            return True
        
        # Победа
        word = session['word']
        user_id = message.from_user.id
        user_name = message.from_user.full_name
        
        # Баллы
        if user_id not in scores: scores[user_id] = {"name": user_name, "points": 0}
        scores[user_id]["points"] += 1
        
        # Удаляем сессию
        del game_sessions[chat_id]
        
        # Формируем топ
        top = sorted(scores.items(), key=lambda x: x[1]['points'], reverse=True)[:5]
        leaderboard = "\n".join([f"{i+1}. {v['name']}: {v['points']}" for i, (k,v) in enumerate(top)])
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔄 Еще раунд", callback_data="cr_restart")
        ]])
        
        await message.answer(
            f"🎉 **ПОБЕДА!**\n\n{user_name} угадал слово: **{word}**\n\n"
            f"🏆 **ТОП ИГРОКОВ:**\n{leaderboard}",
            reply_markup=kb
        )
        return True
    return False
