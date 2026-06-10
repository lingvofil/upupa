from datetime import datetime
import re
import aiofiles
import logging
import random
import collections
from collections import defaultdict
from nltk.util import ngrams
from aiogram import types
from config import LOG_FILE
from prompts import STOPWORDS


STYLE_SAMPLE_MIN_CHARS = 8
RECENT_STYLE_SAMPLE_SIZE = 20
RANDOM_STYLE_SAMPLE_SIZE = 20

def is_style_sample_message(text: str, min_chars: int = STYLE_SAMPLE_MIN_CHARS) -> bool:
    """Возвращает True, если сообщение годится как пример живого пользовательского стиля."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped or stripped.startswith("/"):
        return False
    return len(stripped) >= min_chars

def build_hybrid_style_sample(messages: list, recent_count: int = RECENT_STYLE_SAMPLE_SIZE, random_count: int = RANDOM_STYLE_SAMPLE_SIZE) -> list:
    """Берет последние сообщения для актуальности и случайные из истории для широты лексикона."""
    filtered_messages = [msg.strip() for msg in messages if is_style_sample_message(msg)]
    if not filtered_messages:
        return []

    recent_start = max(len(filtered_messages) - recent_count, 0)
    recent_messages = filtered_messages[recent_start:]
    older_pool = filtered_messages[:recent_start]

    if len(older_pool) <= random_count:
        random_messages = older_pool
    else:
        random_messages = random.sample(older_pool, random_count)

    return recent_messages + random_messages

# Запись сообщений всех пользователей в файл
async def save_user_message(message: types.Message):
    timestamp = datetime.now().isoformat()
    chat_id = message.chat.id if message.chat else "NoChat"
    chat_title = message.chat.title if message.chat and message.chat.title else "ЛС"
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"
    full_name = message.from_user.full_name or "NoName"
    text = message.text or ""
    log_line = f"{timestamp} - Chat {chat_id} ({chat_title}) - User {user_id} ({username}) [{full_name}]: {text}\n"

    try:
        async with aiofiles.open(LOG_FILE, mode="a", encoding="utf-8") as f:
            await f.write(log_line)
    except Exception as e:
        logging.error(f"Ошибка записи в {LOG_FILE}: {e}")

# 📌 Функция для получения сообщений по ID
async def extract_user_messages(user_id: int, chat_id: int) -> list:
    messages = []
    pattern = re.compile(rf".* - Chat {chat_id}\b.*User {user_id}\b.*: (.*)")
    async with aiofiles.open(LOG_FILE, mode="r", encoding="utf-8") as f:
        async for line in f:
            match = pattern.match(line)
            if match:
                messages.append(match.group(1).strip())
    return messages

async def extract_messages_by_username(username: str, chat_id: int) -> list:
    messages = []
    pattern = re.compile(rf".* - Chat {chat_id}\b.*User \d+ \(({re.escape(username)})\) \[.*?\]: (.*)")
    async with aiofiles.open(LOG_FILE, mode="r", encoding="utf-8") as f:
        async for line in f:
            match = pattern.match(line)
            if match:
                messages.append(match.group(2).strip())
    return messages

async def extract_messages_by_full_name(full_name: str, chat_id: int) -> list:
    messages = []
    pattern = re.compile(rf".* - Chat {chat_id}\b.*User \d+ \([^)]+\) \[(.+?)\]: (.*)")
    async with aiofiles.open(LOG_FILE, mode="r", encoding="utf-8") as f:
        async for line in f:
            match = pattern.match(line)
            if match and match.group(1).lower() == full_name.lower():
                messages.append(match.group(2).strip())
    return messages

# Функция для извлечения сообщений всего чата по chat_id
async def extract_chat_messages(chat_id: int) -> list:
    messages = []
    pattern = re.compile(rf".* - Chat {chat_id}\b - User .+?: (.*)")
    async with aiofiles.open(LOG_FILE, mode="r", encoding="utf-8") as f:
        async for line in f:
            match = pattern.match(line)
            if match:
                messages.append(match.group(1).strip())
    return messages

# 📌 Очистка текста (удаление стоп-слов)
def clean_text(text: str) -> list:
    words = re.findall(r"\w+", text.lower())
    filtered_words = [word for word in words if word not in STOPWORDS]
    return filtered_words

# 📌 Функция для получения самых частых слов
async def get_frequent_words(user_id: int, top_n: int = 10):
    messages = await extract_user_messages(user_id)
    all_text = " ".join(messages)
    words = clean_text(all_text)
    counter = collections.Counter(words)
    return counter.most_common(top_n)

# 📌 Функция для получения часто употребляемых фраз
async def get_frequent_phrases(user_id: int, n: int = 2, top_n: int = 10):
    messages = await extract_user_messages(user_id)
    all_text = " ".join(messages)
    words = clean_text(all_text)
    if len(words) < n:
        return []
    ngram_list = list(ngrams(words, n))
    ngram_counter = collections.Counter(ngram_list)
    return [(" ".join(gram), count) for gram, count in ngram_counter.most_common(top_n)]

# 📌 Функция для анализа фраз по тексту (по username/full_name)
def get_frequent_phrases_from_text(text: str, n: int = 2, top_n: int = 5) -> list:
    words = clean_text(text)
    if len(words) < n:
        return []
    ngram_list = list(ngrams(words, n))
    ngram_counter = collections.Counter(ngram_list)
    return [(" ".join(gram), count) for gram, count in ngram_counter.most_common(top_n)]

# Функции для подсчета частотности слов и фраз для чата
async def get_chat_frequent_words(chat_id: int, top_n: int = 10):
    messages = await extract_chat_messages(chat_id)
    all_text = " ".join(messages)
    words = clean_text(all_text)
    counter = collections.Counter(words)
    return counter.most_common(top_n)

async def get_chat_frequent_phrases(chat_id: int, n: int = 2, top_n: int = 10):
    messages = await extract_chat_messages(chat_id)
    all_text = " ".join(messages)
    words = clean_text(all_text)
    if len(words) < n:
        return []
    ngram_list = list(ngrams(words, n))
    ngram_counter = collections.Counter(ngram_list)
    return [(" ".join(gram), count) for gram, count in ngram_counter.most_common(top_n)]

# 🆕 НОВАЯ ФУНКЦИЯ: Получить активных пользователей чата
async def get_chat_active_users(chat_id, min_messages=10):
    """Получить список активных пользователей чата с минимальным количеством сообщений"""
    try:
        user_stats = defaultdict(lambda: {'username': None, 'full_name': None, 'count': 0})
        
        # Паттерн для парсинга строк лога
        pattern = re.compile(rf".* - Chat {chat_id}\b.*User (\d+) \(([^)]+)\) \[(.+?)\]: (.*)")
        
        async with aiofiles.open(LOG_FILE, mode="r", encoding="utf-8") as f:
            async for line in f:
                match = pattern.match(line)
                if match:
                    user_id = match.group(1)
                    username = match.group(2) if match.group(2) != "NoUsername" else None
                    full_name = match.group(3) if match.group(3) != "NoName" else None
                    
                    # Используем username как ключ, если есть, иначе full_name
                    key = username if username else full_name
                    if key:
                        user_stats[key]['username'] = username
                        user_stats[key]['full_name'] = full_name
                        user_stats[key]['count'] += 1
        
        # Фильтруем пользователей с достаточным количеством сообщений
        result = []
        for key, stats in user_stats.items():
            if stats['count'] >= min_messages:
                user_data = {
                    'username': stats['username'],
                    'full_name': stats['full_name'],
                    'message_count': stats['count']
                }
                result.append(user_data)
        
        # Сортируем по количеству сообщений
        result.sort(key=lambda x: x['message_count'], reverse=True)
        
        return result
        
    except Exception as e:
        logging.error(f"Ошибка при получении активных пользователей: {e}")
        return []

# Вынесенная логика обработки "мой лексикон"
async def process_my_lexicon(user_id, chat_id, message):
    # Собираем только сообщения этого пользователя в этом чате
    messages = await extract_user_messages(user_id, chat_id)
    if not messages:
        await message.reply("Нулевой")
        return
        
    frequent_words = get_frequent_phrases_from_text(" ".join(messages), n=1, top_n=10)
    frequent_phrases = get_frequent_phrases_from_text(" ".join(messages), n=2, top_n=5)
    
    response_text = (
        "Часто употребляемые слова в этом чате:\n" +
        ", ".join([f"{word} ({count})" for word, count in frequent_words]) +
        "\n\nЧасто употребляемые фразы:\n" +
        ", ".join([f"{phrase} ({count})" for phrase, count in frequent_phrases])
    )
    
    await message.reply(response_text)

# Вынесенная логика обработки "лексикон чат"
async def process_chat_lexicon(message: types.Message) -> str:
    chat_id = message.chat.id
    frequent_words = await get_chat_frequent_words(chat_id)
    frequent_phrases = await get_chat_frequent_phrases(chat_id, n=2)
    
    response_text = (
        "Часто употребляемые слова в чате:\n" +
        "\n".join([f"{word}: {count}" for word, count in frequent_words]) +
        "\n\nЧасто употребляемые фразы в чате:\n" +
        "\n".join([f"{phrase}: {count}" for phrase, count in frequent_phrases])
    )
    return response_text

# Вынесенная логика обработки "лексикон <имя пользователя>"
async def process_user_lexicon(username_or_name, chat_id, message):
    # Сначала пробуем найти по username (без @)
    messages = await extract_messages_by_username(username_or_name, chat_id)
    
    if not messages:
        # Если по username не нашли, пробуем искать по полному имени
        messages = await extract_messages_by_full_name(username_or_name, chat_id)
        
    if not messages:
        await message.reply(f"Сообщения пользователя '{username_or_name}' в этом чате не найдены.")
        return
        
    frequent_words = get_frequent_phrases_from_text(" ".join(messages), n=1, top_n=10)
    frequent_phrases = get_frequent_phrases_from_text(" ".join(messages), n=2, top_n=5)
    
    response_text = (
        f"Часто употребляемые слова пользователя {username_or_name}:\n" +
        ", ".join([f"{word} ({count})" for word, count in frequent_words]) +
        "\n\nЧасто употребляемые фразы:\n" +
        ", ".join([f"{phrase} ({count})" for phrase, count in frequent_phrases])
    )
    
    await message.reply(response_text)