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

# Кэширование
_templates_cache = []
_template_details = {}

# Пути к шрифтам для Linux (Ubuntu/Debian)
FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
]

def get_font(size):
    """Возвращает жирный шрифт. Если не найден — стандартный."""
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except:
                continue
    # Если шрифтов нет в системе, Pillow вернет крошечный шрифт. 
    # В этом случае текст будет почти незаметен, поэтому рекомендуем установить шрифты:
    # sudo apt-get install fonts-dejavu-core
    return ImageFont.load_default()

def draw_text_in_box(draw, text, box, img_scale, img_width, img_height):
    """Рисует текст, центрируя его в боксе вручную"""
    if not text:
        return

    # Масштабируем координаты из метаданных под реальное фото
    bx = box['x'] * img_scale
    by = box['y'] * img_scale
    bw = box['width'] * img_scale
    bh = box['height'] * img_scale

    # Начальный размер шрифта (примерно 15% от высоты бокса)
    font_size = int(bh * 0.8)
    if font_size < 10: font_size = 20
    
    font = get_font(font_size)
    text = text.upper()

    # Подбор ширины строки (в символах)
    # Примерно: ширина бокса / (размер шрифта * коэффициент)
    avg_char_w = font_size * 0.55
    max_chars = max(1, int(bw / avg_char_w))
    
    lines = textwrap.wrap(text, width=max_chars)
    
    # Уменьшаем шрифт, пока весь блок текста не влезет в высоту бокса
    while len(lines) * (font_size * 1.2) > bh and font_size > 12:
        font_size -= 2
        font = get_font(font_size)
        avg_char_w = font_size * 0.55
        max_chars = max(1, int(bw / avg_char_w))
        lines = textwrap.wrap(text, width=max_chars)

    full_text = "\n".join(lines)

    # Ручной расчет центра (совместимость со старыми версиями Pillow)
    # Используем textbbox для новых версий или textsize для старых
    try:
        left, top, right, bottom = draw.multiline_textbbox((0, 0), full_text, font=font, align="center")
        tw = right - left
        th = bottom - top
    except AttributeError:
        # Фолбек для старых версий Pillow
        tw, th = draw.multiline_textsize(full_text, font=font, align="center")

    # Координаты для рисования (чтобы центр текста совпал с центром бокса)
    draw_x = bx + (bw - tw) / 2
    draw_y = by + (bh - th) / 2

    # Рисуем обводку для читаемости
    stroke_w = max(1, int(font_size * 0.05))
    draw.multiline_text(
        (draw_x, draw_y), 
        full_text, 
        font=font, 
        fill="white", 
        stroke_width=stroke_w, 
        stroke_fill="black", 
        align="center"
    )

async def get_template_info(tid: str):
    if tid in _template_details: return _template_details[tid]
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://api.memegen.link/templates/{tid}", timeout=10)
            if resp.status_code == 200:
                _template_details[tid] = resp.json()
                return _template_details[tid]
    except: pass
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
    """Сбор фраз из истории"""
    source = reply_text
    if not source:
        if os.path.exists(config.LOG_FILE):
            try:
                msgs = []
                with open(config.LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-500:]
                    for line in reversed(lines):
                        match = re.search(r"Chat (\-?\d+).*?\]: (.*?)$", line)
                        if match and match.group(1) == str(chat_id):
                            t = match.group(2).strip()
                            if t and not t.startswith("/") and len(t) > 3:
                                if "мем" not in t.lower(): msgs.append(t)
                        if len(msgs) >= 100: break
                if msgs: source = random.choice(msgs)
            except: pass
    
    if not source: source = "ГДЕ ТЕКСТ | Я НЕ ВИЖУ"
    
    if "|" in source:
        return [p.strip() for p in source.split("|")]
    w = source.split()
    if len(w) > 3:
        m = len(w) // 2
        return [" ".join(w[:m]), " ".join(w[m:])]
    return [source]

async def create_meme_image(chat_id: int, reply_text: str = None) -> BufferedInputFile | None:
    templates = await get_all_templates()
    temp_base = random.choice(templates)
    tid = temp_base['id']
    
    info = await get_template_info(tid)
    if not info: return None
    
    bg_url = f"https://api.memegen.link/images/{tid}.png"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(bg_url, timeout=15)
            if resp.status_code != 200: return None
            
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
            draw = ImageDraw.Draw(img)
            
            # Расчет масштаба
            meta_w = info.get('width', img.size[0])
            scale = img.size[0] / meta_w
            
            texts = get_context_text(chat_id, reply_text)
            boxes = info.get('boxes', [])
            
            for i, box in enumerate(boxes):
                txt = texts[i] if i < len(texts) else texts[-1]
                draw_text_in_box(draw, txt, box, scale, img.size[0], img.size[1])

            out = io.BytesIO()
            img.save(out, format="JPEG", quality=90)
            return BufferedInputFile(out.getvalue(), filename=f"meme_{tid}.jpg")
    except Exception as e:
        logging.error(f"Draw error: {e}")
    return None

async def check_and_send_random_meme(message: Message):
    if not message.text or message.text.startswith("/"): return
    settings = chat_settings.get(str(message.chat.id), {})
    if settings.get("random_memes_enabled", False) and random.random() < 0.01:
        try:
            p = await create_meme_image(message.chat.id)
            if p: await message.answer_photo(p)
        except: pass
