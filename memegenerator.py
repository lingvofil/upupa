import random
import logging
import os
import re
import httpx
import io
from PIL import Image, ImageDraw, ImageFont
from aiogram.types import BufferedInputFile, Message
from config import chat_settings
import config

# Кэш шаблонов
_templates_cache = []

# Пути к шрифтам (обычно есть на Linux серверах)
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
]

def get_font(size):
    """Пытается найти подходящий шрифт в системе"""
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def draw_meme_text(draw, text, position, font, max_width):
    """Рисует текст с черной обводкой (классический мем-стиль)"""
    words = text.split()
    lines = []
    current_line = []
    
    # Простейший перенос строк
    for word in words:
        test_line = " ".join(current_line + [word])
        w, _ = draw.textbbox((0, 0), test_line, font=font)[2:]
        if w <= max_width:
            current_line.append(word)
        else:
            lines.append(" ".join(current_line))
            current_line = [word]
    lines.append(" ".join(current_line))
    
    full_text = "\n".join(lines)
    x, y = position
    
    # Рисуем обводку (8 направлений для жирности)
    stroke_fill = "black"
    thickness = 2
    for ox in range(-thickness, thickness + 1):
        for oy in range(-thickness, thickness + 1):
            draw.text((x + ox, y + oy), full_text, font=font, fill=stroke_fill, align="center", anchor="mm")
    
    # Рисуем основной текст
    draw.text((x, y), full_text, font=font, fill="white", align="center", anchor="mm")

async def get_all_templates():
    global _templates_cache
    if _templates_cache: return _templates_cache
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.memegen.link/templates", timeout=10)
            if resp.status_code == 200:
                _templates_cache = resp.json()
                return _templates_cache
    except: pass
    return [{"id": "drake", "lines": 2}]

def get_context_text(chat_id: int, reply_text: str = None) -> str:
    """Выбирает случайную фразу из последних 100 сообщений чата"""
    if reply_text: return reply_text
    log_path = config.LOG_FILE
    if not os.path.exists(log_path): return "Логи пусты"
    
    try:
        messages = []
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-500:] # Читаем последние 500 строк лога
            for line in reversed(lines):
                match = re.search(r"Chat (\-?\d+).*?\]: (.*?)$", line)
                if match:
                    cid, txt = match.groups()
                    if cid == str(chat_id):
                        txt = txt.strip()
                        if txt and not txt.startswith("/") and len(txt) > 3:
                            if not any(x in txt.lower() for x in ["мем", "meme"]):
                                messages.append(txt)
                if len(messages) >= 100: break # Собираем до 100 фраз
        return random.choice(messages) if messages else "Тишина..."
    except: return "Ошибка парсинга"

async def create_meme_image(chat_id: int, reply_text: str = None) -> BufferedInputFile | None:
    source_text = get_context_text(chat_id, reply_text)
    templates = await get_all_templates()
    template = random.choice(templates)
    tid = template.get("id", "drake")
    
    # Скачиваем ЧИСТЫЙ шаблон без вотермарки
    bg_url = f"https://api.memegen.link/images/{tid}.jpg"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(bg_url, timeout=15)
            if resp.status_code != 200: return None
            
            # Обработка изображения через Pillow
            img = Image.open(io.BytesIO(resp.content))
            draw = ImageDraw.Draw(img)
            width, height = img.size
            
            # Настройка шрифта (динамический размер от высоты картинки)
            font_size = int(height * 0.08)
            font = get_font(font_size)
            
            # Разбивка текста на верх и низ
            words = source_text.split()
            if len(words) > 3:
                mid = len(words) // 2
                top_text = " ".join(words[:mid]).upper()
                bottom_text = " ".join(words[mid:]).upper()
            else:
                top_text = ""
                bottom_text = source_text.upper()
            
            # Рисуем
            if top_text:
                draw_meme_text(draw, top_text, (width/2, height*0.15), font, width*0.9)
            if bottom_text:
                draw_meme_text(draw, bottom_text, (width/2, height*0.85), font, width*0.9)
                
            # Сохранение результата в буфер
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=90)
            return BufferedInputFile(output.getvalue(), filename=f"meme_{tid}.jpg")
            
    except Exception as e:
        logging.error(f"Pillow meme error: {e}")
    return None

async def check_and_send_random_meme(message: Message):
    if not message.text or message.text.startswith("/"): return
    settings = chat_settings.get(str(message.chat.id), {})
    if settings.get("random_memes_enabled", False) and random.random() < 0.01:
        try:
            await message.bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
            photo = await create_meme_image(message.chat.id)
            if photo:
                await message.answer_photo(photo)
        except Exception as e:
            logging.error(f"Random meme error: {e}")
