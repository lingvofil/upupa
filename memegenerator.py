import random
import logging
import os
import re
import httpx
from aiogram.types import BufferedInputFile
import config

# Кэш шаблонов
_templates_cache = []

def _prep_text(text: str) -> str:
    """
    Подготовка текста для memegen.link.
    Заменяет спецсимволы по правилам API и делает URL-safe кодирование.
    """
    if not text:
        return "_"
    
    text = text.strip()
    # Очистка от лишних пробелов и переносов
    text = re.sub(r"\s+", " ", text)
    text = text[:100]

    # Правила замены memegen.link
    replacements = [
        ("-", "--"),
        ("_", "__"),
        (" ", "_"),
        ("?", "~q"),
        ("&", "~a"),
        ("%", "~p"),
        ("#", "~h"),
        ("/", "~s"),
        ("\\", "~b"),
        ("<", "~l"),
        (">", "~g"),
        ('"', "''"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    
    # Важно: memegen ожидает именно такие символы в пути, 
    # дополнительное url-кодирование всей строки может сломать рендер кириллицы.
    return text

async def get_all_templates():
    """Загружает список шаблонов"""
    global _templates_cache
    if _templates_cache:
        return _templates_cache
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.memegen.link/templates", timeout=10)
            if resp.status_code == 200:
                _templates_cache = resp.json()
                return _templates_cache
    except Exception as e:
        logging.error(f"Meme templates fetch error: {e}")
    
    return [{"id": "drake", "lines": 2}, {"id": "two-buttons", "lines": 2}]

def get_context_text(chat_id: int, reply_text: str = None) -> str:
    """Выбирает текст из истории или реплая"""
    if reply_text:
        return reply_text

    log_path = config.LOG_FILE
    if not os.path.exists(log_path):
        return "Когда не нашел сообщений в логе"

    try:
        messages = []
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-100:] # Читаем последние 100 строк
            for line in reversed(lines):
                match = re.search(r"Chat (\-?\d+).*?\]: (.*?)$", line)
                if match:
                    cid, txt = match.groups()
                    if cid == str(chat_id):
                        txt = txt.strip()
                        if txt and not txt.startswith("/") and len(txt) > 3:
                            if "мем" not in txt.lower():
                                messages.append(txt)
                if len(messages) > 15: break
        
        return random.choice(messages) if messages else "Пустота в чате..."
    except:
        return "Что-то пошло не так"

async def create_meme_image(chat_id: int, reply_text: str = None) -> BufferedInputFile | None:
    """Генерирует мем и возвращает файл для Telegram"""
    source_text = get_context_text(chat_id, reply_text)
    templates = await get_all_templates()
    template = random.choice(templates)
    
    tid = template.get("id", "drake")
    
    # Разбивка текста
    words = source_text.split()
    if len(words) > 2:
        mid = len(words) // 2
        top = _prep_text(" ".join(words[:mid]))
        bottom = _prep_text(" ".join(words[mid:]))
    else:
        top = "_"
        bottom = _prep_text(source_text)

    # Собираем URL. Параметр ?font=notosans критичен для кириллицы!
    # Также используем .png для лучшего качества текста.
    url = f"https://api.memegen.link/images/{tid}/{top}/{bottom}.png?font=notosans"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=15)
            if resp.status_code == 200:
                return BufferedInputFile(resp.content, filename=f"meme_{tid}.png")
            else:
                logging.error(f"Memegen API error {resp.status_code} for {url}")
    except Exception as e:
        logging.error(f"Meme download error: {e}")
    
    return None
