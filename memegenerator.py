import os
import re
import json
import random
import asyncio
import logging
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai

# === ИМПОРТЫ ИЗ ТВОЕЙ СТРУКТУРЫ ===
import config
import prompts

# Настраиваем Gemini, используя ключ из твоего Config.py
# В твоем конфиге ключ называется GENERIC_API_KEY
genai.configure(api_key=Config.GENERIC_API_KEY)

# Инициализируем модель специально для мемов.
# Используем 'gemini-2.0-flash' как самую быструю и стабильную для JSON задач.
model = genai.GenerativeModel('gemini-2.0-flash')

# --- КОНФИГУРАЦИЯ МОДУЛЯ ---
FONT_PATH = "assets/fonts/impact.ttf"
MEME_DIR = "assets/memes/"

# Шаблоны и координаты (x1, y1, x2, y2)
TEMPLATES = {
    "2_lines": [
        {
            "file": "drake.jpg",
            "boxes": [(300, 20, 580, 250), (300, 260, 580, 500)]
        },
        {
            "file": "buttons.jpg",
            "boxes": [(20, 70, 200, 180), (230, 60, 420, 150)] 
        },
        {
            "file": "pooh.jpg",
            "boxes": [(350, 50, 750, 250), (350, 350, 750, 550)] 
        }
    ],
    "1_line": [
        {
            "file": "change.jpg",
            "boxes": [(270, 220, 550, 350)]
        },
        {
            "file": "sign.jpg",
            "boxes": [(150, 100, 500, 350)]
        }
    ]
}

def get_last_chat_messages(chat_id_str, limit=10):
    """
    Парсит лог-файл (Config.LOG_FILE) и возвращает последние N сообщений.
    """
    log_path = Config.LOG_FILE
    messages = []
    
    if not os.path.exists(log_path):
        logging.error(f"Log file not found at {log_path}")
        return []

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Идем с конца файла к началу
        for line in reversed(lines):
            try:
                # Твой Regex из summarize.py/Config
                match = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+) - Chat (\-?\d+) \((.*?)\) - User (\d+) \((.*?)\) \[(.*?)\]: (.*?)$", line)
                
                if match:
                    _, log_chat_id, _, _, username, display_name, text = match.groups()
                    
                    if str(log_chat_id) == str(chat_id_str):
                        text = text.strip()
                        if text and "мем" not in text.lower(): # Исключаем саму команду
                            name = display_name if display_name and display_name != "None" else username
                            messages.append(f"{name}: {text}")
                            
                    if len(messages) >= limit:
                        break
            except Exception:
                continue
                
        return list(reversed(messages))

    except Exception as e:
        logging.error(f"Error parsing logs for meme: {e}")
        return []

def fit_text_to_box(draw, text, box, font_path, max_font_size=60, min_font_size=15):
    """Рисует текст внутри box, подбирая размер шрифта и переносы."""
    x1, y1, x2, y2 = box
    box_width = x2 - x1
    box_height = y2 - y1
    
    font_size = max_font_size
    text = text.upper()
    
    while font_size >= min_font_size:
        try:
            font = ImageFont.truetype(font_path, font_size)
        except:
            font = ImageFont.load_default()
            break

        lines = []
        words = text.split()
        current_line = []
        valid = True
        
        for word in words:
            test_line = ' '.join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if (bbox[2] - bbox[0]) <= box_width:
                current_line.append(word)
            else:
                if not current_line: 
                    valid = False
                    break
                lines.append(' '.join(current_line))
                current_line = [word]
                bbox_word = draw.textbbox((0, 0), word, font=font)
                if (bbox_word[2] - bbox_word[0]) > box_width:
                    valid = False
                    break
        
        if valid and current_line:
            lines.append(' '.join(current_line))

        text_height = len(lines) * (font_size * 1.2)
        if valid and text_height <= box_height:
            # Отрисовка
            current_y = y1 + (box_height - text_height) / 2
            stroke_width = max(2, int(font_size / 15))
            
            for line in lines:
                bbox_line = draw.textbbox((0, 0), line, font=font)
                line_width = bbox_line[2] - bbox_line[0]
                current_x = x1 + (box_width - line_width) / 2
                
                draw.text((current_x, current_y), line, font=font, fill="white", stroke_width=stroke_width, stroke_fill="black")
                current_y += font_size * 1.2
            return True
            
        font_size -= 2
        
    return False

async def generate_meme_content(chat_history_text: str) -> dict:
    """Генерирует JSON с текстом мема через Gemini (используя Config.API_KEY)."""
    # Используем промпт из Prompts.py
    prompt = f"{Prompts.MEME_SYSTEM_PROMPT}\n\nВот последние сообщения чата:\n{chat_history_text}"
    
    try:
        response = await model.generate_content_async(prompt)
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        
        # Попытка найти JSON, если модель добавила лишний текст
        start = clean_json.find('{')
        end = clean_json.rfind('}') + 1
        if start != -1 and end != -1:
            clean_json = clean_json[start:end]
            
        return json.loads(clean_json)
    except Exception as e:
        logging.error(f"Gemini Meme Error: {e}")
        return None

async def create_meme_image(data: dict):
    """Выбирает шаблон и создает файл."""
    meme_type = data.get("type", "2_lines")
    texts = []
    
    if meme_type == "2_lines":
        texts = [data.get("line1", ""), data.get("line2", "")]
        templates_list = TEMPLATES["2_lines"]
    else:
        texts = [data.get("text", "")]
        templates_list = TEMPLATES["1_line"]
        
    template = random.choice(templates_list)
    img_path = os.path.join(MEME_DIR, template["file"])
    
    if not os.path.exists(img_path):
        logging.error(f"Template not found: {img_path}")
        return None

    try:
        with Image.open(img_path) as img:
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)
            
            for i, text in enumerate(texts):
                if i < len(template["boxes"]):
                    fit_text_to_box(draw, text, template["boxes"][i], FONT_PATH)
            
            output_path = f"temp_meme_{random.randint(10000, 99999)}.jpg"
            img.save(output_path)
            return output_path
    except Exception as e:
        logging.error(f"Drawing Error: {e}")
        return None

async def process_meme_command(chat_id, reply_text=None):
    """
    Главная функция вызова.
    """
    meme_data = {}
    
    if reply_text:
        meme_data = {
            "type": "1_line",
            "text": reply_text
        }
    else:
        history_msgs = get_last_chat_messages(str(chat_id), limit=10)
        if not history_msgs:
            return None
        history_text = "\n".join(history_msgs)
        meme_data = await generate_meme_content(history_text)
        
    if not meme_data:
        return None
        
    return await create_meme_image(meme_data)
