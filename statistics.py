# statistics.py
import sqlite3
from datetime import datetime
import asyncio
from typing import Dict, Any

from Config import DB_FILE # Добавьте в Config.py путь к файлу БД

# Функция для инициализации БД
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
            is_private BOOLEAN NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

# Асинхронная функция для записи сообщения в БД
async def log_message(chat_id: int, user_id: int, message_type: str, is_private: bool):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        timestamp = datetime.now()
        cursor.execute(
            "INSERT INTO message_stats (chat_id, user_id, message_timestamp, message_type, is_private) VALUES (?, ?, ?, ?, ?)",
            (chat_id, user_id, timestamp, message_type, is_private)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging message: {e}") # Здесь можно подключить ваш логгер

# Функции для получения статистики (примеры)
async def get_total_messages_per_chat() -> Dict[int, int]:
    """Возвращает словарь {chat_id: message_count}."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, COUNT(*) FROM message_stats GROUP BY chat_id ORDER BY COUNT(*) DESC")
    data = {row[0]: row[1] for row in cursor.fetchall()}
    conn.close()
    return data

async def get_activity_by_hour() -> Dict[int, int]:
    """Возвращает почасовую активность."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # В SQLite для извлечения часа используется strftime('%H', ...)
    cursor.execute("SELECT strftime('%H', message_timestamp), COUNT(*) FROM message_stats GROUP BY strftime('%H', message_timestamp)")
    data = {int(row[0]): row[1] for row in cursor.fetchall()}
    conn.close()
    return data