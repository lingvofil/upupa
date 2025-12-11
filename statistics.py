# statistics.py
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, Awaitable
import logging

# --- НОВЫЕ ИМПОРТЫ ДЛЯ MIDDLEWARE ---
from aiogram import BaseMiddleware
from aiogram.types import Message
# ------------------------------------

from config import DB_FILE, ADMIN_ID

# --- НОВЫЙ БЛОК: MIDDLEWARE ДЛЯ ОГРАНИЧЕНИЯ В ЛС ---
private_message_timestamps: Dict[int, datetime] = {}
PRIVATE_MESSAGE_COOLDOWN = timedelta(hours=1)

class PrivateRateLimitMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        if event.chat.type == 'private' and event.from_user:
            user_id = event.from_user.id
            if user_id == ADMIN_ID:
                return await handler(event, data)

            now = datetime.now()
            last_message_time = private_message_timestamps.get(user_id)

            if last_message_time:
                if now - last_message_time < PRIVATE_MESSAGE_COOLDOWN:
                    await event.reply("иди общайся в чат, хитрый педераст")
                    return
            
            private_message_timestamps[user_id] = now
        return await handler(event, data)

# --- Инициализация Базы Данных ---

def init_db():
    """Инициализирует БД и обновляет схему таблицы, если это необходимо."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Таблица общей статистики сообщений
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS message_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            message_timestamp TIMESTAMP NOT NULL,
            message_type TEXT NOT NULL,
            is_private BOOLEAN NOT NULL,
            chat_title TEXT,
            user_name TEXT,
            user_username TEXT
        )
    ''')

    # 2. НОВАЯ ТАБЛИЦА: Статистика запросов к модели (Gemini)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS model_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            chat_id BIGINT,
            user_id BIGINT,
            model_name TEXT,
            request_type TEXT -- например, 'generate_content' или 'chat_message'
        )
    ''')
    
    # Миграции для message_stats (на случай старой базы)
    try:
        cursor.execute("ALTER TABLE message_stats ADD COLUMN chat_title TEXT")
    except sqlite3.OperationalError: pass
    try:
        cursor.execute("ALTER TABLE message_stats ADD COLUMN user_name TEXT")
    except sqlite3.OperationalError: pass
    try:
        cursor.execute("ALTER TABLE message_stats ADD COLUMN user_username TEXT")
    except sqlite3.OperationalError: pass

    conn.commit()
    conn.close()

# --- Логирование Сообщений ---

async def log_message(chat_id: int, user_id: int, message_type: str, is_private: bool,
                      chat_title: Optional[str], user_name: str, user_username: Optional[str]):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        timestamp = datetime.now()
        cursor.execute(
            """INSERT INTO message_stats 
               (chat_id, user_id, message_timestamp, message_type, is_private, chat_title, user_name, user_username) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, user_id, timestamp, message_type, is_private, chat_title, user_name, user_username)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging message: {e}")

# --- НОВАЯ ФУНКЦИЯ: Логирование запросов к модели ---

def log_model_request(chat_id: Optional[int], user_id: Optional[int], model_name: str, request_type: str = "unknown"):
    """Записывает факт обращения к LLM."""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        timestamp = datetime.now()
        cursor.execute(
            """INSERT INTO model_stats 
               (timestamp, chat_id, user_id, model_name, request_type) 
               VALUES (?, ?, ?, ?, ?)""",
            (timestamp, chat_id, user_id, model_name, request_type)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Failed to log model request: {e}")

# --- Получение Статистики ---

def get_stats(period_hours: Optional[int] = None) -> Dict[str, Dict]:
    """Получает статистику сообщений + статистику модели."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    params = []
    time_filter = ""
    if period_hours is not None:
        cutoff = datetime.now() - timedelta(hours=period_hours)
        time_filter = "WHERE message_timestamp >= ?"
        params.append(cutoff)

    # 1. Обычные сообщения (группы)
    query_groups = f"""
        SELECT COALESCE(chat_title, chat_id), COUNT(*) 
        FROM message_stats 
        WHERE is_private = 0 {f"AND message_timestamp >= ?" if period_hours else ""}
        GROUP BY COALESCE(chat_title, chat_id)
        ORDER BY COUNT(*) DESC
    """
    cursor.execute(query_groups, params if period_hours else [])
    group_stats = {str(row[0]): row[1] for row in cursor.fetchall()}

    # 2. Обычные сообщения (ЛС)
    query_private = f"""
        SELECT user_name, user_username, COUNT(*) 
        FROM message_stats 
        WHERE is_private = 1 {f"AND message_timestamp >= ?" if period_hours else ""}
        GROUP BY user_id, user_name, user_username 
        ORDER BY COUNT(*) DESC
    """
    cursor.execute(query_private, params if period_hours else [])
    private_stats = {}
    for row in cursor.fetchall():
        display = f"{row[0]} (@{row[1]})" if row[1] else row[0]
        private_stats[display] = row[2]

    # 3. НОВОЕ: Статистика запросов к Gemini
    # Считаем, кто (user_id) и где (chat_id) больше всего дергает модель
    model_params = []
    model_filter = ""
    if period_hours is not None:
        model_cutoff = datetime.now() - timedelta(hours=period_hours)
        model_filter = "WHERE timestamp >= ?"
        model_params.append(model_cutoff)

    query_model = f"""
        SELECT chat_id, COUNT(*)
        FROM model_stats
        {model_filter}
        GROUP BY chat_id
        ORDER BY COUNT(*) DESC
    """
    cursor.execute(query_model, model_params)
    # Пытаемся сопоставить ID чата с именем из таблицы message_stats, если есть
    raw_model_stats = cursor.fetchall()
    model_stats_result = {}
    
    for chat_id, count in raw_model_stats:
        if not chat_id:
            key = "Неизвестный чат"
        else:
            # Пытаемся найти имя чата
            cursor.execute("SELECT chat_title FROM message_stats WHERE chat_id = ? LIMIT 1", (chat_id,))
            res = cursor.fetchone()
            key = f"{res[0]} (ID: {chat_id})" if res and res[0] else f"ID: {chat_id}"
        model_stats_result[key] = count

    conn.close()
    return {"groups": group_stats, "private": private_stats, "model_usage": model_stats_result}

async def get_total_messages() -> Dict[str, Dict]:
    return get_stats()

async def get_messages_last_24_hours() -> Dict[str, Dict]:
    return get_stats(period_hours=24)

async def get_messages_last_hour() -> Dict[str, Dict]:
    return get_stats(period_hours=1)

async def get_activity_by_hour(period_hours: Optional[int] = None) -> Dict[int, int]:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    params = []
    time_filter = ""
    if period_hours is not None:
        time_filter = "WHERE message_timestamp >= ?"
        params.append(datetime.now() - timedelta(hours=period_hours))

    query = f"""
        SELECT strftime('%H', message_timestamp), COUNT(*) 
        FROM message_stats 
        {time_filter} 
        GROUP BY strftime('%H', message_timestamp)
    """
    cursor.execute(query, params)
    data = {hour: 0 for hour in range(24)}
    for row in cursor.fetchall():
        data[int(row[0])] = row[1]
    conn.close()
    return data
