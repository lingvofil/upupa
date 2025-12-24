import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, Awaitable
import logging

from aiogram import BaseMiddleware
from aiogram.types import Message

from config import DB_FILE, ADMIN_ID

# --- Rate limit для ЛС ---
private_message_timestamps: Dict[int, datetime] = {}
PRIVATE_MESSAGE_COOLDOWN = timedelta(hours=1)

class PrivateRateLimitMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data):
        if event.chat.type == 'private' and event.from_user:
            user_id = event.from_user.id
            if user_id != ADMIN_ID:
                now = datetime.now()
                last = private_message_timestamps.get(user_id)
                if last and now - last < PRIVATE_MESSAGE_COOLDOWN:
                    await event.reply("иди общайся в чат, хитрый педераст")
                    return
                private_message_timestamps[user_id] = now
        return await handler(event, data)

# --- Инициализация БД ---

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS model_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            chat_id BIGINT,
            user_id BIGINT,
            model_name TEXT,
            request_type TEXT
        )
    ''')

    conn.commit()
    conn.close()

# --- Логирование использования нейросетей (Gemini) ---

def log_model_request(chat_id: Optional[int], user_id: Optional[int], model_name: str, request_type: str):
    """
    Функция для записи статистики использования моделей нейросетей.
    Вызывается из config.py.
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO model_stats (chat_id, user_id, model_name, request_type) 
               VALUES (?, ?, ?, ?)""",
            (chat_id, user_id, model_name, request_type)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Error logging model request: {e}")

# --- Логирование сообщений ---

async def log_message(chat_id: int, user_id: int, message_type: str, is_private: bool,
                      chat_title: Optional[str], user_name: str, user_username: Optional[str]):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO message_stats 
               (chat_id, user_id, message_timestamp, message_type, is_private, chat_title, user_name, user_username) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, user_id, datetime.now(), message_type, is_private, chat_title, user_name, user_username)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Error logging message: {e}")

# --- ВСПОМОГАТЕЛЬНОЕ: получение последнего имени пользователя ---

def get_last_known_user_display(conn, user_id: int) -> str:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT user_name, user_username
        FROM message_stats
        WHERE user_id = ?
        ORDER BY message_timestamp DESC
        LIMIT 1
        """,
        (user_id,)
    )
    row = cursor.fetchone()

    if not row:
        return f"User {user_id}"

    name, username = row
    if username:
        return f"{name} (@{username})" if name else f"@{username}"
    if name:
        return name
    return f"User {user_id}"

# --- Получение статистики ---

def get_stats(period_hours: Optional[int] = None) -> Dict[str, Dict]:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    params = []
    time_filter = ""
    if period_hours is not None:
        time_filter = "AND message_timestamp >= ?"
        params.append(datetime.now() - timedelta(hours=period_hours))

    # --- Группы ---
    cursor.execute(f"""
        SELECT COALESCE(chat_title, chat_id), COUNT(*)
        FROM message_stats
        WHERE is_private = 0 {time_filter}
        GROUP BY COALESCE(chat_title, chat_id)
        ORDER BY COUNT(*) DESC
    """, params)

    group_stats = {str(row[0]): row[1] for row in cursor.fetchall()}

    # --- ЛС ---
    cursor.execute(f"""
        SELECT user_id, COUNT(*)
        FROM message_stats
        WHERE is_private = 1 {time_filter}
        GROUP BY user_id
        ORDER BY COUNT(*) DESC
    """, params)

    private_stats = {}
    for user_id, count in cursor.fetchall():
        display = get_last_known_user_display(conn, user_id)
        private_stats[display] = count

    # --- Статистика модели ---
    model_time_filter = ""
    model_params = []
    if period_hours:
        model_time_filter = "WHERE timestamp >= ?"
        model_params.append(datetime.now() - timedelta(hours=period_hours))

    cursor.execute(f"""
        SELECT chat_id, COUNT(*)
        FROM model_stats
        {model_time_filter}
        GROUP BY chat_id
        ORDER BY COUNT(*) DESC
    """, model_params)

    model_usage = {}
    for chat_id, count in cursor.fetchall():
        if not chat_id:
            key = "Неизвестный чат / API"
        else:
            cursor.execute(
                "SELECT chat_title FROM message_stats WHERE chat_id = ? LIMIT 1",
                (chat_id,)
            )
            res = cursor.fetchone()
            key = res[0] if res and res[0] else f"ID: {chat_id}"
        model_usage[key] = count

    conn.close()
    return {
        "groups": group_stats,
        "private": private_stats,
        "model_usage": model_usage
    }

async def get_total_messages():
    return get_stats()

async def get_messages_last_24_hours():
    return get_stats(period_hours=24)

async def get_messages_last_hour():
    return get_stats(period_hours=1)

async def get_activity_by_hour(period_hours: Optional[int] = None) -> Dict[int, int]:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    params = []
    time_filter = ""
    if period_hours is not None:
        time_filter = "WHERE message_timestamp >= ?"
        params.append(datetime.now() - timedelta(hours=period_hours))

    cursor.execute(f"""
        SELECT strftime('%H', message_timestamp), COUNT(*)
        FROM message_stats
        {time_filter}
        GROUP BY strftime('%H', message_timestamp)
    """, params)

    data = {hour: 0 for hour in range(24)}
    for hour, count in cursor.fetchall():
        data[int(hour)] = count

    conn.close()
    return data
