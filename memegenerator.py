import random
import logging
import os
import re
import httpx  # Используем httpx для асинхронности
from aiogram.types import BufferedInputFile
import config

# Настройки кэширования шаблонов
_templates_cache = []

def _escape_memegen_text(text: str) -> str:
    """
    Экранирование текста по правилам memegen.link
    """
    if not text:
        return "_"
    
    # Очистка
    text = text.strip()
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text[:100] # Ограничение длины

    # Специфические замены Memegen
    replacements = [
        ("-", "--"),   # дефис -> двойной дефис
        ("_", "__"),   # подчеркивание -> двойное подчеркивание
        (" ", "_"),    # пробел -> подчеркивание
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
        
    return text

async def get_all_templates():
    """Загружает актуальный список шаблонов с API"""
    global _templates_cache
    if _templates_cache:
        return _templates_cache
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.memegen.link/templates", timeout=5)
            if resp.status_code == 200:
                _templates_cache = resp.json()
                return _templates_cache
    except Exception as e:
        logging.error(f"Error fetching meme templates: {e}")
    
    # Резервный список, если API лежит
    return [{"id": "drake", "lines": 2}, {"id": "two-buttons", "lines": 2}]

def get_last_chat_messages(chat_id_str: str, limit: int = 15) -> list[str]:
    """Извлечение сообщений из лога для контекста"""
    log_path = config.LOG_FILE
    messages: list[str] = []

    if not os.path.exists(log_path):
        return []

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in reversed(lines):
            # Регулярка под ваш формат лога
            match = re.search(r"Chat (\-?\d+).*?\]: (.*?)$", line)
            if not match: continue

            log_chat_id, text = match.groups()
            if str(log_chat_id) != str(chat_id_str): continue

            text = text.strip()
            # Игнорируем команды и пустые строки
            if not text or text.startswith("/") or len(text) < 2: continue
            if any(cmd in text.lower() for cmd in ["мем", "meme"]): continue

            messages.append(text)
            if len(messages) >= limit: break

        return messages
    except Exception as e:
        logging.error(f"Log parse error: {e}")
        return []

async def create_meme_image(chat_id: int, reply_text: str = None) -> BufferedInputFile | None:
    """
    Основная функция: выбирает текст, шаблон и скачивает готовую картинку.
    Возвращает BufferedInputFile для отправки через Telegram.
    """
    # 1. Сбор текстов
    history = get_last_chat_messages(str(chat_id))
    
    if reply_text:
        # Если есть реплай, используем его как основу
        text_source = reply_text
    elif history:
        text_source = random.choice(history)
    else:
        return None

    # 2. Выбор шаблона
    templates = await get_all_templates()
    template = random.choice(templates)
    template_id = template.get("id", "drake")
    lines_count = template.get("lines", 2)

    # 3. Подготовка строк для мема
    words = text_source.split()
    if lines_count >= 2 and len(words) > 1:
        mid = len(words) // 2
        t0 = _escape_memegen_text(" ".join(words[:mid]))
        t1 = _escape_memegen_text(" ".join(words[mid:]))
        url = f"https://api.memegen.link/images/{template_id}/{t0}/{t1}.jpg"
    else:
        t0 = _escape_memegen_text(text_source)
        url = f"https://api.memegen.link/images/{template_id}/{t0}.jpg"

    # 4. Скачивание
    try:
        async with httpx.AsyncClient() as client:
            # Добавляем параметры, чтобы картинка была качественнее или с фолбеком
            resp = await client.get(url, timeout=10)
            if resp.status_code != 200:
                logging.error(f"Memegen API returned {resp.status_code} for URL: {url}")
                return None
            
            return BufferedInputFile(resp.content, filename=f"meme_{template_id}.jpg")
    except Exception as e:
        logging.error(f"Failed to download meme: {e}")
        return None
