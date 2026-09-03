from collections import defaultdict, deque
from datetime import datetime
import collections
import logging
import random
import re

import aiofiles
from aiogram import types
from nltk.util import ngrams

from core.paths import USER_MESSAGES_LOG_PATH as LOG_FILE
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


def _reservoir_add(
    reservoir: list[tuple[int, str]],
    item: tuple[int, str],
    seen: int,
    capacity: int,
) -> None:
    """Добавить элемент в равновероятную bounded reservoir-выборку."""
    if capacity <= 0:
        return
    if len(reservoir) < capacity:
        reservoir.append(item)
        return
    replacement_index = random.randrange(seen)
    if replacement_index < capacity:
        reservoir[replacement_index] = item


async def _extract_messages(
    pattern: re.Pattern,
    text_group: int,
    *,
    predicate=None,
    sample_size: int | None = None,
    recent_size: int = 0,
) -> list[str]:
    """
    Потоково извлечь сообщения.

    Без sample_size сохраняется legacy-поведение и возвращается вся выборка.
    При sample_size память ограничена: часть выборки равномерно семплируется,
    а recent_size последних сообщений гарантированно остаются в конце списка.
    """
    if sample_size is not None and sample_size <= 0:
        return []

    if sample_size is None:
        messages: list[str] = []
        async with aiofiles.open(LOG_FILE, mode="r", encoding="utf-8") as f:
            async for line in f:
                match = pattern.match(line)
                if match and (predicate is None or predicate(match)):
                    messages.append(match.group(text_group).strip())
        return messages

    recent_capacity = min(max(recent_size, 0), sample_size)
    reservoir_capacity = sample_size - recent_capacity
    recent: deque[tuple[int, str]] = deque(maxlen=recent_capacity or None)
    reservoir: list[tuple[int, str]] = []
    older_seen = 0
    sequence = 0

    async with aiofiles.open(LOG_FILE, mode="r", encoding="utf-8") as f:
        async for line in f:
            match = pattern.match(line)
            if not match or (predicate is not None and not predicate(match)):
                continue

            text = match.group(text_group).strip()
            sequence += 1

            if recent_capacity:
                if len(recent) == recent_capacity:
                    displaced = recent.popleft()
                    older_seen += 1
                    _reservoir_add(reservoir, displaced, older_seen, reservoir_capacity)
                recent.append((sequence, text))
            else:
                older_seen += 1
                _reservoir_add(reservoir, (sequence, text), older_seen, reservoir_capacity)

    reservoir.sort(key=lambda item: item[0])
    return [text for _index, text in reservoir] + [text for _index, text in recent]


# 📌 Функция для получения сообщений по ID
async def extract_user_messages(
    user_id: int,
    chat_id: int,
    *,
    sample_size: int | None = None,
    recent_size: int = 0,
) -> list:
    pattern = re.compile(rf".* - Chat {chat_id}\b.*User {user_id}\b.*: (.*)")
    return await _extract_messages(
        pattern,
        1,
        sample_size=sample_size,
        recent_size=recent_size,
    )


async def extract_messages_by_username(
    username: str,
    chat_id: int,
    *,
    sample_size: int | None = None,
    recent_size: int = 0,
) -> list:
    pattern = re.compile(rf".* - Chat {chat_id}\b.*User \d+ \(({re.escape(username)})\) \[.*?\]: (.*)")
    return await _extract_messages(
        pattern,
        2,
        sample_size=sample_size,
        recent_size=recent_size,
    )


async def extract_messages_by_full_name(
    full_name: str,
    chat_id: int,
    *,
    sample_size: int | None = None,
    recent_size: int = 0,
) -> list:
    pattern = re.compile(rf".* - Chat {chat_id}\b.*User \d+ \([^)]+\) \[(.+?)\]: (.*)")
    return await _extract_messages(
        pattern,
        2,
        predicate=lambda match: match.group(1).lower() == full_name.lower(),
        sample_size=sample_size,
        recent_size=recent_size,
    )


# Функция для извлечения сообщений всего чата по chat_id
async def extract_chat_messages(
    chat_id: int,
    *,
    sample_size: int | None = None,
    recent_size: int = 0,
) -> list:
    pattern = re.compile(rf".* - Chat {chat_id}\b.*User .+?: (.*)")
    return await _extract_messages(
        pattern,
        1,
        sample_size=sample_size,
        recent_size=recent_size,
    )


# 📌 Очистка текста (удаление стоп-слов)
def clean_text(text: str) -> list:
    words = re.findall(r"\w+", text.lower())
    return [word for word in words if word not in STOPWORDS]


async def _stream_lexicon_stats(
    pattern: re.Pattern,
    text_group: int,
    *,
    predicate=None,
    n_values: tuple[int, ...] = (1, 2),
) -> tuple[dict[int, collections.Counter], int]:
    """Считать n-граммы по логу без materialize всей истории/всех n-грамм."""
    counters = {n: collections.Counter() for n in n_values}
    tails = {n: [] for n in n_values if n > 1}
    matched_messages = 0

    async with aiofiles.open(LOG_FILE, mode="r", encoding="utf-8") as f:
        async for line in f:
            match = pattern.match(line)
            if not match or (predicate is not None and not predicate(match)):
                continue

            matched_messages += 1
            words = clean_text(match.group(text_group))
            if not words:
                continue

            for n in n_values:
                if n == 1:
                    counters[n].update(words)
                    continue

                combined = tails[n] + words
                counters[n].update(ngrams(combined, n))
                tails[n] = combined[-(n - 1):]

    return counters, matched_messages


# 📌 Функция для получения самых частых слов
async def get_frequent_words(user_id: int, top_n: int = 10):
    # Legacy API не знает chat_id; оставляем историческое поведение вызова.
    messages = await extract_user_messages(user_id)
    all_text = " ".join(messages)
    words = clean_text(all_text)
    counter = collections.Counter(words)
    return counter.most_common(top_n)


# 📌 Функция для получения часто употребляемых фраз
async def get_frequent_phrases(user_id: int, n: int = 2, top_n: int = 10):
    # Legacy API не знает chat_id; оставляем историческое поведение вызова.
    messages = await extract_user_messages(user_id)
    all_text = " ".join(messages)
    words = clean_text(all_text)
    if len(words) < n:
        return []
    ngram_counter = collections.Counter(ngrams(words, n))
    return [(" ".join(gram), count) for gram, count in ngram_counter.most_common(top_n)]


# 📌 Функция для анализа фраз по тексту (по username/full_name)
def get_frequent_phrases_from_text(text: str, n: int = 2, top_n: int = 5) -> list:
    words = clean_text(text)
    if len(words) < n:
        return []
    ngram_counter = collections.Counter(ngrams(words, n))
    return [(" ".join(gram), count) for gram, count in ngram_counter.most_common(top_n)]


# Функции для подсчета частотности слов и фраз для чата
async def get_chat_frequent_words(chat_id: int, top_n: int = 10):
    pattern = re.compile(rf".* - Chat {chat_id}\b.*User .+?: (.*)")
    counters, _matched = await _stream_lexicon_stats(pattern, 1, n_values=(1,))
    return counters[1].most_common(top_n)


async def get_chat_frequent_phrases(chat_id: int, n: int = 2, top_n: int = 10):
    pattern = re.compile(rf".* - Chat {chat_id}\b.*User .+?: (.*)")
    counters, _matched = await _stream_lexicon_stats(pattern, 1, n_values=(n,))
    return [(" ".join(gram), count) for gram, count in counters[n].most_common(top_n)]


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
        for stats in user_stats.values():
            if stats['count'] >= min_messages:
                result.append({
                    'username': stats['username'],
                    'full_name': stats['full_name'],
                    'message_count': stats['count']
                })

        # Сортируем по количеству сообщений
        result.sort(key=lambda x: x['message_count'], reverse=True)
        return result

    except Exception as e:
        logging.error(f"Ошибка при получении активных пользователей: {e}")
        return []


async def _user_lexicon_stats(user_id: int, chat_id: int):
    pattern = re.compile(rf".* - Chat {chat_id}\b.*User {user_id}\b.*: (.*)")
    return await _stream_lexicon_stats(pattern, 1, n_values=(1, 2))


async def _named_user_lexicon_stats(username_or_name: str, chat_id: int):
    username_pattern = re.compile(
        rf".* - Chat {chat_id}\b.*User \d+ \(({re.escape(username_or_name)})\) \[.*?\]: (.*)"
    )
    counters, matched = await _stream_lexicon_stats(username_pattern, 2, n_values=(1, 2))
    if matched:
        return counters, matched

    full_name_pattern = re.compile(rf".* - Chat {chat_id}\b.*User \d+ \([^)]+\) \[(.+?)\]: (.*)")
    return await _stream_lexicon_stats(
        full_name_pattern,
        2,
        predicate=lambda match: match.group(1).lower() == username_or_name.lower(),
        n_values=(1, 2),
    )


# Вынесенная логика обработки "мой лексикон"
async def process_my_lexicon(user_id, chat_id, message):
    counters, matched = await _user_lexicon_stats(user_id, chat_id)
    if not matched:
        await message.reply("Нулевой")
        return

    frequent_words = [(word, count) for word, count in counters[1].most_common(10)]
    frequent_phrases = [(" ".join(gram), count) for gram, count in counters[2].most_common(5)]

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
    pattern = re.compile(rf".* - Chat {chat_id}\b.*User .+?: (.*)")
    counters, _matched = await _stream_lexicon_stats(pattern, 1, n_values=(1, 2))
    frequent_words = counters[1].most_common(10)
    frequent_phrases = [(" ".join(gram), count) for gram, count in counters[2].most_common(10)]

    response_text = (
        "Часто употребляемые слова в чате:\n" +
        "\n".join([f"{word}: {count}" for word, count in frequent_words]) +
        "\n\nЧасто употребляемые фразы в чате:\n" +
        "\n".join([f"{phrase}: {count}" for phrase, count in frequent_phrases])
    )
    return response_text


# Вынесенная логика обработки "лексикон <имя пользователя>"
async def process_user_lexicon(username_or_name, chat_id, message):
    counters, matched = await _named_user_lexicon_stats(username_or_name, chat_id)
    if not matched:
        await message.reply(f"Сообщения пользователя '{username_or_name}' в этом чате не найдены.")
        return

    frequent_words = [(word, count) for word, count in counters[1].most_common(10)]
    frequent_phrases = [(" ".join(gram), count) for gram, count in counters[2].most_common(5)]

    response_text = (
        f"Часто употребляемые слова пользователя {username_or_name}:\n" +
        ", ".join([f"{word} ({count})" for word, count in frequent_words]) +
        "\n\nЧасто употребляемые фразы:\n" +
        ", ".join([f"{phrase} ({count})" for phrase, count in frequent_phrases])
    )

    await message.reply(response_text)