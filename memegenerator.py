import random
import logging
import os
import re
import httpx
import io
import textwrap
from PIL import Image, ImageDraw, ImageFont
from aiogram.types import BufferedInputFile, Message
from config import chat_settings
import config

# Кэш шаблонов и их настроек
_templates_cache = []
_template_details = {}

# Шрифты (добавьте свой путь, еслиImpact.ttf лежит в папке бота)
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "Impact.ttf", # Если положите файл шрифта в корень
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
]

def get_font(size):
    for path in FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

def draw_text_in_box(draw, text, box, font_base_size):
    """Рисует текст внутри заданного прямоугольника (box)"""
    x, y, w, h = box['x'], box['y'], box['width'], box['height']
    
    # Центр бокса
    center_x = x + w / 2
    center_y = y + h / 2
    
    # Автоподбор размера шрифта под бокс
    current_size = font_base_size
    font = get_font(current_size)
    
    # Разбиваем на строки
    # Примерная ширина символа ~0.6 от размера шрифта
    char_width = current_size * 0.5
    chars_per_line = max(1, int(w / char_width))
    lines = textwrap.wrap(text, width=chars_per_line)
    
    # Рисуем строки
    full_text = "\n".join(lines).upper()
    
    # Рисуем обводку
    stroke = 2
    for ox in range(-stroke, stroke + 1):
        for oy in range(-stroke, stroke + 1):
            draw.text((center_x + ox, center_y + oy), full_text, font=font, 
                      fill="black", align="center", anchor="mm")
    
    # Основной текст
    draw.text((center_x, center_y), full_text, font=font, 
              fill="white", align="center", anchor="mm")

async def get_template_info(tid: str):
    """Получает детальную информацию о зонах текста для шаблона"""
    if tid in _template_details:
        return _template_details[tid]
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://api.memegen.link/templates/{tid}", timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                _template_details[tid] = data
                return data
    except Exception as e:
        logging.error(f"Error fetching template details for {tid}: {e}")
    return None

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
    return [{"id": "drake"}]

def get_context_text(chat_id: int, reply_text: str = None) -> list[str]:
    """Возвращает список фраз для заполнения боксов мема"""
    source_text = ""
    if reply_text:
        source_text = reply_text
    else:
        log_path = config.LOG_FILE
        if os.path.exists(log_path):
            try:
                messages = []
                with open(log_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-500:]
                    for line in reversed(lines):
                        match = re.search(r"Chat (\-?\d+).*?\]: (.*?)$", line)
                        if match and match.group(1) == str(chat_id):
                            txt = match.group(2).strip()
                            if txt and not txt.startswith("/") and len(txt) > 3:
                                if not any(x in txt.lower() for x in ["мем", "meme"]):
                                    messages.append(txt)
                        if len(messages) >= 100: break
                if messages:
                    source_text = random.choice(messages)
            except: pass

    if not source_text:
        source_text = "Когда нет слов | одни эмоции"

    # Если в шаблоне несколько зон, попробуем разделить текст по разделителю или пополам
    if "|" in source_text:
        return [part.strip() for part in source_text.split("|", 3)]
    
    words = source_text.split()
    if len(words) > 3:
        mid = len(words) // 2
        return [" ".join(words[:mid]), " ".join(words[mid:])]
    return [source_text]

async def create_meme_image(chat_id: int, reply_text: str = None) -> BufferedInputFile | None:
    # 1. Выбираем шаблон
    all_templates = await get_all_templates()
    template_base = random.choice(all_templates)
    tid = template_base['id']
    
    # 2. Получаем зоны (boxes) для этого шаблона
    info = await get_template_info(tid)
    if not info: return None
    boxes = info.get('boxes', [])
    
    # 3. Получаем тексты
    text_parts = get_context_text(chat_id, reply_text)
    
    # 4. Скачиваем чистый фон
    bg_url = f"https://api.memegen.link/images/{tid}.png"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(bg_url, timeout=15)
            if resp.status_code != 200: return None
            
            img = Image.open(io.BytesIO(resp.content))
            draw = ImageDraw.Draw(img)
            
            # Базовый размер шрифта зависит от высоты картинки
            base_size = int(img.size[1] * 0.07)
            
            # Рисуем текст в каждый доступный бокс
            for i, box in enumerate(boxes):
                if i < len(text_parts):
                    txt = text_parts[i]
                else:
                    txt = text_parts[-1] if text_parts else "???"
                
                draw_text_in_box(draw, txt, box, base_size)

            output = io.BytesIO()
            img.save(output, format="JPEG", quality=95)
            return BufferedInputFile(output.getvalue(), filename=f"meme_{tid}.jpg")
            
    except Exception as e:
        logging.error(f"Meme generation failed: {e}")
    return None

async def check_and_send_random_meme(message: Message):
    if not message.text or message.text.startswith("/"): return
    settings = chat_settings.get(str(message.chat.id), {})
    if settings.get("random_memes_enabled", False) and random.random() < 0.01:
        try:
            photo = await create_meme_image(message.chat.id)
            if photo:
                await message.answer_photo(photo)
        except Exception as e:
            logging.error(f"Random meme error: {e}")
