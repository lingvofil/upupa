import random
import logging
import os
import re
import httpx
from aiogram.types import BufferedInputFile, Message
from config import chat_settings
import config

# Кэш шаблонов
_templates_cache = []

def _prep_text(text: str) -> str:
    """Подготовка текста для API memegen.link (экранирование)"""
    if not text:
        return "_"
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = text[:100]
    replacements = [
        ("-", "--"), ("_", "__"), (" ", "_"), ("?", "~q"),
        ("&", "~a"), ("%", "~p"), ("#", "~h"), ("/", "~s"),
        ("\\", "~b"), ("<", "~l"), (">", "~g"), ('"', "''"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text

async def get_all_templates():
    """Загружает список шаблонов с API"""
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
    """Выбирает текст из истории логов или реплая"""
    if reply_text:
        return reply_text

    log_path = config.LOG_FILE
    if not os.path.exists(log_path):
        return "Когда забыл настроить логи"

    try:
        messages = []
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-100:]
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
        return random.choice(messages) if messages else "Тишина в эфире"
    except:
        return "Ошибка парсинга реальности"

async def create_meme_image(chat_id: int, reply_text: str = None) -> BufferedInputFile | None:
    """Генерирует мем и возвращает объект файла"""
    source_text = get_context_text(chat_id, reply_text)
    templates = await get_all_templates()
    template = random.choice(templates)
    tid = template.get("id", "drake")
    
    words = source_text.split()
    if len(words) > 2:
        mid = len(words) // 2
        top = _prep_text(" ".join(words[:mid]))
        bottom = _prep_text(" ".join(words[mid:]))
    else:
        top = "_"
        bottom = _prep_text(source_text)

    url = f"https://api.memegen.link/images/{tid}/{top}/{bottom}.png?font=notosans"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=15)
            if resp.status_code == 200:
                return BufferedInputFile(resp.content, filename=f"meme_{tid}.png")
    except Exception as e:
        logging.error(f"Meme download error: {e}")
    return None

async def check_and_send_random_meme(message: Message):
    """
    Проверяет настройки чата и с шансом 1% отправляет мем.
    Вызывается из основного обработчика сообщений.
    """
    chat_id_str = str(message.chat.id)
    settings = chat_settings.get(chat_id_str, {})
    
    # Если функция выключена в настройках — выходим
    if not settings.get("random_memes_enabled", False):
        return

    # Шанс 1% (0.01)
    if random.random() < 0.01:
        try:
            # Имитируем деятельность
            await message.bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
            
            photo = await create_meme_image(message.chat.id)
            if photo:
                await message.answer_photo(photo)
                logging.info(f"Random meme sent to chat {message.chat.id}")
        except Exception as e:
            logging.error(f"Error in check_and_send_random_meme: {e}")
