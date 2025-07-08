# statistics.py
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

# Убедитесь, что в Config.py добавлена переменная DB_FILE
# Например: DB_FILE = 'bot_stats.db'
try:
    from Config import DB_FILE
except ImportError:
    DB_FILE = "bot_stats.db"

# --- Инициализация Базы Данных ---

def init_db():
    """Инициализирует БД и обновляет схему таблицы, если это необходимо."""
    with sqlite3.connect(DB_FILE) as conn:
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
        
        # Добавляем новые столбцы, если они отсутствуют
        for column in ["chat_title", "user_name", "user_username"]:
            try:
                cursor.execute(f"ALTER TABLE message_stats ADD COLUMN {column} TEXT")
            except sqlite3.OperationalError:
                pass  # Столбец уже существует
        conn.commit()

# --- Логирование Сообщений ---

async def log_message(chat_id: int, user_id: int, message_type: str, is_private: bool,
                      chat_title: Optional[str], user_name: str, user_username: Optional[str]):
    """Записывает информацию о сообщении в базу данных."""
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            timestamp = datetime.now()
            cursor.execute(
                """INSERT INTO message_stats 
                   (chat_id, user_id, message_timestamp, message_type, is_private, chat_title, user_name, user_username) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (chat_id, user_id, timestamp, message_type, is_private, chat_title, user_name, user_username)
            )
            conn.commit()
    except Exception as e:
        print(f"Error logging message: {e}")

# --- Получение Статистики ---

def get_stats(period_hours: Optional[int] = None) -> Dict[str, Dict]:
    """
    Получает статистику сообщений за указанный период.
    Группирует по ID, но для отображения использует последнее известное имя/название.
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        
        params = []
        time_filter = ""
        if period_hours is not None:
            time_filter = "WHERE message_timestamp >= ?"
            params.append(datetime.now() - timedelta(hours=period_hours))

        # Статистика по групповым чатам
        query_groups = f"""
            SELECT 
                t.chat_id,
                (SELECT chat_title FROM message_stats WHERE chat_id = t.chat_id AND chat_title IS NOT NULL ORDER BY message_timestamp DESC LIMIT 1) as last_title,
                COUNT(*)
            FROM message_stats as t
            WHERE t.is_private = 0 
            {'AND t.message_timestamp >= ?' if period_hours is not None else ''}
            GROUP BY t.chat_id
        """
        cursor.execute(query_groups, params if period_hours is not None else [])
        group_stats = {}
        for chat_id, chat_title, count in cursor.fetchall():
            display_name = chat_title if chat_title else f"Чат ({chat_id})"
            group_stats[display_name] = count

        # Статистика по личным сообщениям
        query_private = f"""
            SELECT 
                t.user_id,
                (SELECT user_name FROM message_stats WHERE user_id = t.user_id AND user_name IS NOT NULL ORDER BY message_timestamp DESC LIMIT 1) as last_user_name,
                (SELECT user_username FROM message_stats WHERE user_id = t.user_id ORDER BY message_timestamp DESC LIMIT 1) as last_user_username,
                COUNT(*)
            FROM message_stats as t
            WHERE t.is_private = 1 
            {'AND t.message_timestamp >= ?' if period_hours is not None else ''}
            GROUP BY t.user_id
        """
        cursor.execute(query_private, params if period_hours is not None else [])
        private_stats = {}
        for user_id, user_name, user_username, count in cursor.fetchall():
            if user_name:
                display_name = f"{user_name} (@{user_username})" if user_username else user_name
            else:
                display_name = f"Пользователь ({user_id})"
            private_stats[display_name] = count

    return {"groups": group_stats, "private": private_stats}

async def get_total_messages() -> Dict[str, Dict]:
    """Возвращает статистику сообщений за все время."""
    return get_stats()

async def get_messages_last_24_hours() -> Dict[str, Dict]:
    """Возвращает статистику сообщений за последние 24 часа."""
    return get_stats(period_hours=24)

async def get_messages_last_hour() -> Dict[str, Dict]:
    """Возвращает статистику сообщений за последний час."""
    return get_stats(period_hours=1)

async def get_activity_by_hour(period_hours: Optional[int] = None) -> Dict[int, int]:
    """Возвращает активность по часам."""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        
        params = []
        time_filter = ""
        if period_hours is not None:
            time_filter = "WHERE message_timestamp >= ?"
            params.append(datetime.now() - timedelta(hours=period_hours))

        query = f"SELECT strftime('%H', message_timestamp), COUNT(*) FROM message_stats {time_filter} GROUP BY strftime('%H', message_timestamp)"
        cursor.execute(query, params)
        data = {hour: 0 for hour in range(24)}
        for row in cursor.fetchall():
            data[int(row[0])] = row[1]
            
    return data
