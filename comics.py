# comics.py
import random
import re
from datetime import datetime, timedelta
from aiogram import types
from PIL import Image, ImageDraw, ImageFont
import textwrap
import os
from config import LOG_FILE

# Шаблоны комиксов (можно расширить)
COMIC_TEMPLATES = [
    {
        "name": "two_panel",
        "panels": 2,
        "width": 800,
        "height": 400,
        "panel_positions": [(0, 0, 400, 400), (400, 0, 400, 400)],
        "text_positions": [(200, 350), (600, 350)]
    },
    {
        "name": "three_panel",
        "panels": 3,
        "width": 1200,
        "height": 400,
        "panel_positions": [(0, 0, 400, 400), (400, 0, 400, 400), (800, 0, 400, 400)],
        "text_positions": [(200, 350), (600, 350), (1000, 350)]
    },
    {
        "name": "vertical_two",
        "panels": 2,
        "width": 400,
        "height": 800,
        "panel_positions": [(0, 0, 400, 400), (0, 400, 400, 400)],
        "text_positions": [(200, 350), (200, 750)]
    }
]

def parse_log_messages(hours_back=24):
    """Парсит сообщения из лог-файла за указанное количество часов"""
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    
    messages = []
    cutoff_time = datetime.now() - timedelta(hours=hours_back)
    
    for line in lines:
        # Парсим строку лога
        match = re.match(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+) - Chat (-?\d+) \((.+?)\) - User (\d+) \((.+?)\) \[(.+?)\]: (.+)', line.strip())
        if match:
            timestamp_str, chat_id, chat_name, user_id, username, display_name, message_text = match.groups()
            
            # Парсим timestamp
            try:
                timestamp = datetime.fromisoformat(timestamp_str)
                if timestamp > cutoff_time:
                    messages.append({
                        'timestamp': timestamp,
                        'chat_id': int(chat_id),
                        'user_id': int(user_id),
                        'username': username,
                        'display_name': display_name,
                        'text': message_text
                    })
            except ValueError:
                continue
    
    return messages

def filter_suitable_messages(messages, min_length=10, max_length=100):
    """Фильтрует сообщения подходящие для комиксов"""
    suitable = []
    
    for msg in messages:
        text = msg['text']
        
        # Исключаем команды, ссылки, спам
        if (text.startswith('/') or 
            text.startswith('http') or 
            len(text) < min_length or 
            len(text) > max_length or
            text.count('😀') > 3):  # много эмодзи
            continue
            
        suitable.append(msg)
    
    return suitable

def create_comic_image(template, messages):
    """Создает изображение комикса"""
    # Создаем базовое изображение
    img = Image.new('RGB', (template['width'], template['height']), color='white')
    draw = ImageDraw.Draw(img)
    
    # Пытаемся загрузить шрифт
    try:
        font = ImageFont.truetype("arial.ttf", 16)
        name_font = ImageFont.truetype("arial.ttf", 14)
    except:
        font = ImageFont.load_default()
        name_font = ImageFont.load_default()
    
    # Рисуем панели комикса
    for i, (x1, y1, x2, y2) in enumerate(template['panel_positions']):
        # Рамка панели
        draw.rectangle([x1, y1, x2, y2], outline='black', width=2)
        
        if i < len(messages):
            msg = messages[i]
            text_x, text_y = template['text_positions'][i]
            
            # Имя персонажа
            draw.text((text_x - 100, text_y - 40), f"{msg['display_name']}:", 
                     fill='blue', font=name_font, anchor='mm')
            
            # Текст сообщения (с переносами)
            wrapped_text = textwrap.fill(msg['text'], width=30)
            draw.text((text_x, text_y), wrapped_text, fill='black', font=font, anchor='mm')
    
    return img

def generate_random_comic(chat_id, hours_back=24):
    """Генерирует случайный комикс из сообщений чата"""
    # Получаем сообщения из лога
    all_messages = parse_log_messages(hours_back)
    
    # Фильтруем по чату
    chat_messages = [msg for msg in all_messages if msg['chat_id'] == chat_id]
    
    if len(chat_messages) < 2:
        return None, "Недостаточно сообщений для создания комикса"
    
    # Фильтруем подходящие сообщения
    suitable_messages = filter_suitable_messages(chat_messages)
    
    if len(suitable_messages) < 2:
        return None, "Не найдено подходящих сообщений для комикса"
    
    # Выбираем случайный шаблон
    template = random.choice(COMIC_TEMPLATES)
    
    # Выбираем случайные сообщения
    selected_messages = random.sample(suitable_messages, min(template['panels'], len(suitable_messages)))
    
    # Создаем комикс
    comic_image = create_comic_image(template, selected_messages)
    
    # Сохраняем временно
    comic_path = f"temp_comic_{chat_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    comic_image.save(comic_path)
    
    return comic_path, None

async def handle_comic_command(message: types.Message):
    """Обработчик команды комикс"""
    try:
        comic_path, error = generate_random_comic(message.chat.id)
        
        if error:
            await message.reply(f"❌ {error}")
            return
        
        if comic_path and os.path.exists(comic_path):
            # Отправляем комикс
            with open(comic_path, 'rb') as photo:
                await message.reply_photo(
                    photo=photo,
                    caption="🎭 Случайный комикс из диалогов чата!"
                )
            
            # Удаляем временный файл
            os.remove(comic_path)
        else:
            await message.reply("❌ Не удалось создать комикс")
            
    except Exception as e:
        await message.reply(f"❌ Ошибка при создании комикса: {str(e)}")

async def handle_comic_history_command(message: types.Message):
    """Обработчик команды для комикса из истории (больше времени)"""
    try:
        # Берем сообщения за неделю
        comic_path, error = generate_random_comic(message.chat.id, hours_back=168)
        
        if error:
            await message.reply(f"❌ {error}")
            return
        
        if comic_path and os.path.exists(comic_path):
            with open(comic_path, 'rb') as photo:
                await message.reply_photo(
                    photo=photo,
                    caption="🎭 Комикс из истории чата (неделя)!"
                )
            
            os.remove(comic_path)
        else:
            await message.reply("❌ Не удалось создать комикс")
            
    except Exception as e:
        await message.reply(f"❌ Ошибка при создании комикса: {str(e)}")

def get_comic_stats(chat_id):
    """Получает статистику для комиксов"""
    messages = parse_log_messages(168)  # За неделю
    chat_messages = [msg for msg in messages if msg['chat_id'] == chat_id]
    suitable = filter_suitable_messages(chat_messages)
    
    return {
        'total_messages': len(chat_messages),
        'suitable_messages': len(suitable),
        'available_templates': len(COMIC_TEMPLATES)
    }