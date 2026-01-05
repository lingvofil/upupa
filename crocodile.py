#crocodile.py
import os
import random
import logging
import socketio
import asyncio
import base64
import urllib.parse
from aiohttp import web
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from config import model, bot 

# ================== НАСТРОЙКИ ==================
WEB_APP_DOMAIN = "invitations-adjusted-eggs-banana.trycloudflare.com"
WEB_APP_SHORT_NAME = "upupadile" 
BOT_USERNAME = "expertyebaniebot"
SOCKET_SERVER_PORT = 8080

game_sessions = {} 
scores = {}        

# ================== ЧАСТЬ 1: WebSocket Сервер ==================
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

@sio.event
async def send_frame(sid, data):
    """Прием скриншота и отправка в чат"""
    room_id = data.get('room')
    image_data = data.get('image')
    
    if not room_id or not image_data:
        return

    # Логируем получение кадра для отладки
    print(f"📸 Получен скриншот для комнаты {room_id} (длина: {len(image_data)})")

    try:
        # Превращаем m123 обратно в -123
        chat_id = room_id.replace("m", "-") if room_id.startswith("m") else room_id
        session = game_sessions.get(str(chat_id))
        
        if session:
            header, encoded = image_data.split(",", 1)
            data_bytes = base64.b64decode(encoded)
            
            photo = BufferedInputFile(data_bytes, filename="draw.jpg")
            
            # Отправляем новый скриншот
            new_msg = await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=f"🖌 **{session.get('drawer_name')}** рисует...\nУгадывайте слово в чате!",
                disable_notification=True
            )
            
            # Удаляем старый скриншот
            if session.get('last_photo_id'):
                try: await bot.delete_message(chat_id, session['last_photo_id'])
                except: pass
            
            session['last_photo_id'] = new_msg.message_id
        else:
            print(f"⚠️ Сессия для чата {chat_id} не найдена в game_sessions")
    except Exception as e:
        logging.error(f"Error in send_frame: {e}")

async def serve_index(request):
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, 'index.html')
    return web.FileResponse(file_path) if os.path.exists(file_path) else web.Response(status=404)

app_game.router.add_get("/game", serve_index)

async def start_socket_server():
    runner = web.AppRunner(app_game)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', SOCKET_SERVER_PORT).start()
    logging.info(f"=== Crocodile Socket Server started on 8080 ===")

# ================== ЧАСТЬ 2: Логика Игры ==================

async def generate_game_word():
    prompt = "Придумай одно существительное на русском языке для игры Крокодил. Одно слово."
    try:
        def sync_call(): return model.generate_content(prompt)
        response = await asyncio.to_thread(sync_call)
        word = response.text.strip().lower().split()[0]
        return "".join(filter(str.isalpha, word))
    except:
        return random.choice(["трактор", "кактус", "пельмень", "бегемот", "телевизор"])

def get_game_keyboard(chat_id):
    safe_cid = str(chat_id).replace("-", "m")
    app_link = f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}?startapp={safe_cid}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Открыть холст", url=app_link)],
        [
            InlineKeyboardButton(text="👁 Словцо", callback_data=f"cr_w_{chat_id}"),
            InlineKeyboardButton(text="🔄 Другое", callback_data=f"cr_n_{chat_id}")
        ]
    ])

async def handle_start_game(message: types.Message):
    chat_id = str(message.chat.id)
    word = await generate_game_word()
    game_sessions[chat_id] = {
        "word": word,
        "drawer_id": message.from_user.id,
        "drawer_name": message.from_user.full_name,
        "last_photo_id": None
    }
    await message.answer(
        f"🎮 **КРОКОДИЛ НАЧАТ!**\n\nВедущий: {message.from_user.full_name}\nУгадывайте слово!",
        reply_markup=get_game_keyboard(chat_id)
    )

async def handle_callback(callback: types.CallbackQuery):
    chat_id = callback.data.split("_")[-1]
    session = game_sessions.get(chat_id)
    if not session: return await callback.answer("Игра окончена.")
    if callback.data.startswith("cr_w_"):
        if callback.from_user.id != session['drawer_id']:
            return await callback.answer("Это не твое слово!", show_alert=True)
        await callback.answer(f"СЛОВО: {session['word'].upper()}", show_alert=True)
    elif callback.data.startswith("cr_n_"):
        if callback.from_user.id != session['drawer_id']: return await callback.answer("Только ведущий!")
        session['word'] = await generate_game_word()
        await callback.answer("Слово заменено!")

async def check_answer(message: types.Message):
    chat_id = str(message.chat.id)
    session = game_sessions.get(chat_id)
    if not session or not message.text: return False
    if message.text.strip().lower() == session['word']:
        if message.from_user.id == session['drawer_id']: return True
        
        user_id, user_name, word = message.from_user.id, message.from_user.full_name, session['word']
        if user_id not in scores: scores[user_id] = {"name": user_name, "points": 0}
        scores[user_id]["points"] += 1
        
        del game_sessions[chat_id]
        top = sorted(scores.items(), key=lambda x: x[1]['points'], reverse=True)[:5]
        leaderboard = "\n".join([f"{i+1}. {v['name']}: {v['points']}" for i, (k,v) in enumerate(top)])
        
        await message.answer(f"🎉 **ПОБЕДА!**\n{user_name} угадал: **{word}**\n\n🏆 **ТОП:**\n{leaderboard}",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Еще!", callback_data="cr_restart")]]))
        return True
    return False
