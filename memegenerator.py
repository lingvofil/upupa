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

# Список путей к шрифтам для Linux (основные сервера)
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
]

def get_font(size):
    """Возвращает жирный шрифт указанного размера"""
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except:
                continue
    return ImageFont.load_default()

def draw_text_in_box(draw, text, box, img_scale):
    """Рисует текст, вписанный в координаты бокса с учетом масштаба"""
    # Масштабируем координаты бокса под реальный размер картинки
    x = box['x'] * img_scale
    y = box['y'] * img_scale
    w = box['width'] * img_scale
    h = box['height'] * img_scale
    
    center_x = x + w / 2
    center_y = y + h / 2
    
    # 1. Подбираем размер шрифта под высоту и ширину бокса
    # Начинаем с 10% от высоты картинки и уменьшаем, пока не влезет
    font_size = int(h * 0.8) # Текст не должен занимать 100% высоты
    font = get_font(font_size)
    
    # 2. Перенос строк
    # Примерная ширина символа ~0.5 от кегля
    avg_char_width = font_size * 0.5
    max_chars = max(1, int(w / avg_char_width))
    lines = textwrap.wrap(text.upper(), width=max_chars)
    
    # Если строк слишком много, уменьшаем шрифт
    while len(lines) * font_size > h * 1.1 and font_size > 10:
        font_size -= 2
        font = get_font(font_size)
        avg_char_width = font_size * 0.5
        max_chars = max(1, int(w / avg_char_width))
        lines = textwrap.wrap(text.upper(), width=max_chars)

    full_text = "\n".join(lines)
    
    # 3. Рисуем классическую мемную обводку
    stroke = max(1, int(font_size * 0.05))
    draw.multiline_text(
        (center_x, center_y), 
        full_text, 
        font=font, 
        fill="white", 
        stroke_width=stroke, 
        stroke_fill="black", 
        align="center", 
        anchor="mm"
    )

async def get_template_info(tid: str):
    """Получает данные о зонах текста для шаблона"""
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
        logging.error(f"Template info error ({tid}): {e}")
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
    """Собирает до 100 фраз и возвращает список для заполнения мема"""
    source_text = reply_text
    
    if not source_text:
        log_path = config.LOG_FILE
        if os.path.exists(log_path):
            try:
                messages = []
                with open(log_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-500:] # Читаем последние 500 строк
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
        source_text = "Я | ТВОЙ БОТ"

    # Разделяем текст для разных зон мема
    if "|" in source_text:
        return [part.strip() for part in source_text.split("|")]
    
    words = source_text.split()
    if len(words) > 3:
        mid = len(words) // 2
        return [" ".join(words[:mid]), " ".join(words[mid:])]
    return [source_text]

async def create_meme_image(chat_id: int, reply_text: str = None) -> BufferedInputFile | None:
    all_templates = await get_all_templates()
    template_base = random.choice(all_templates)
    tid = template_base['id']
    
    info = await get_template_info(tid)
    if not info: return None
    
    # 1. Скачиваем чистый фон
    bg_url = f"https://api.memegen.link/images/{tid}.png"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(bg_url, timeout=15)
            if resp.status_code != 200: return None
            
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            draw = ImageDraw.Draw(img)
            
            # 2. Считаем масштаб (реальная ширина / ширина в метаданных)
            meta_width = info.get('width', img.size[0])
            img_scale = img.size[0] / meta_width
            
            # 3. Подготавливаем тексты
            text_parts = get_context_text(chat_id, reply_text)
            boxes = info.get('boxes', [])
            
            # 4. Рисуем текст в каждый бокс
            for i, box in enumerate(boxes):
                # Берем соответствующую часть текста или последнюю доступную
                txt = text_parts[i] if i < len(text_parts) else text_parts[-1]
                draw_text_in_box(draw, txt, box, img_scale)

            # 5. Сохраняем
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=95)
            return BufferedInputFile(output.getvalue(), filename=f"meme_{tid}.jpg")
            
    except Exception as e:
        logging.error(f"Pillow drawing error: {e}")
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
            logging.error(f"Auto-meme error: {e}")
