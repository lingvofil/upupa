import random
import logging
import os
import re
import httpx
from aiogram.types import BufferedInputFile, Message
from config import chat_settings
import config

# Кэш шаблонов для производительности
_templates_cache = []

def _prep_text(text: str) -> str:
    """Экранирование текста по правилам memegen.link для корректного рендеринга"""
    if not text:
        return "_"
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    text = text[:100]
    # Правила замены спецсимволов API
    replacements = [
        ("-", "--"), ("_", "__"), (" ", "_"), ("?", "~q"),
        ("&", "~a"), ("%", "~p"), ("#", "~h"), ("/", "~s"),
        ("\\", "~b"), ("<", "~l"), (">", "~g"), ('"', "''"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text

async def get_all_templates():
    """Получает список всех доступных шаблонов мемов"""
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
    """Выбирает текст для мема из реплая или случайную фразу из логов чата"""
    if reply_text:
        return reply_text

    log_path = config.LOG_FILE
    if not os.path.exists(log_path):
        return "Когда логи пусты, как мой кошелек"

    try:
        messages = []
        chat_id_str = str(chat_id)
        
        with open(log_path, "r", encoding="utf-8") as f:
            # Читаем хвост файла, но обрабатываем аккуратно
            lines = f.readlines()
            # Берем последние 1000 строк для большего выбора
            target_lines = lines[-1000:] if len(lines) > 1000 else lines
            
            for line in reversed(target_lines):
                # Проверяем наличие Chat ID в строке
                if chat_id_str in line:
                    # Универсальная регулярка, как в history_engine
                    # Ищем текст после последнего закрытия квадратной скобки или двоеточия
                    match = re.search(r"\]:\s*(.*)$", line)
                    if match:
                        txt = match.group(1).strip()
                        # Игнорируем команды, короткие фразы и системные сообщения
                        if txt and not txt.startswith("/") and len(txt) > 3:
                            # Убираем сообщения, где упоминается сам мем
                            if not any(x in txt.lower() for x in ["мем", "meme"]):
                                messages.append(txt)
                
                if len(messages) >= 50: 
                    break
        
        if messages:
            return random.choice(messages)
        return "Где все?"
        
    except Exception as e:
        logging.error(f"Meme context error: {e}")
        return "Ошибка 404: Юмор не найден"

async def create_meme_image(chat_id: int, reply_text: str = None) -> BufferedInputFile | None:
    """Формирует URL мема, скачивает его и возвращает файл"""
    source_text = get_context_text(chat_id, reply_text)
    templates = await get_all_templates()
    template = random.choice(templates)
    tid = template.get("id", "drake")
    
    # Логика разделения текста на верх/низ
    words = source_text.split()
    if len(words) > 2:
        mid = len(words) // 2
        top = _prep_text(" ".join(words[:mid]))
        bottom = _prep_text(" ".join(words[mid:]))
    else:
        top = "_"
        bottom = _prep_text(source_text)

    # font=notosans обязателен для кириллицы
    url = f"https://api.memegen.link/images/{tid}/{top}/{bottom}.png?font=notosans"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=15)
            if resp.status_code == 200:
                return BufferedInputFile(resp.content, filename=f"meme_{tid}.png")
            logging.error(f"Memegen error {resp.status_code} for {url}")
    except Exception as e:
        logging.error(f"Meme download error: {e}")
    return None

async def check_and_send_random_meme(message: Message):
    """Проверка условий и отправка мема"""
    if not message.text or message.text.startswith("/"):
        return

    chat_id_str = str(message.chat.id)
    settings = chat_settings.get(chat_id_str, {})
    
    if not settings.get("random_memes_enabled", False):
        return

    meme_prob = settings.get("meme_prob", 0.01)

    if random.random() < meme_prob:
        try:
            await message.bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
            photo = await create_meme_image(message.chat.id)
            if photo:
                await message.answer_photo(photo)
                logging.info(f"Random meme triggered in chat {message.chat.id}")
        except Exception as e:
            logging.error(f"Random meme trigger error: {e}")
