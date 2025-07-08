import sqlite3
import re
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

# Попытка импорта из Config, с запасным вариантом на случай отсутствия
try:
    from Config import LOG_FILE, DB_FILE
except ImportError:
    logging.warning("Не удалось импортировать LOG_FILE, DB_FILE из Config.py. Используются значения по умолчанию.")
    LOG_FILE = "user_messages.log"
    DB_FILE = "bot_stats.db"

# Регулярные выражения для парсинга лога
CHAT_REGEX = re.compile(r"Chat (-?\d+) \((.*?)\)")
USER_REGEX = re.compile(r"User (\d+) \((.*?)\)")

def _update_db_from_log():
    """Обновляет пустые записи в БД статистики данными из лог-файла."""
    chat_titles = {}
    user_names = {}
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                chat_match = CHAT_REGEX.search(line)
                if chat_match:
                    chat_id, chat_title = int(chat_match.group(1)), chat_match.group(2).strip()
                    if chat_id and chat_title:
                        chat_titles[chat_id] = chat_title

                user_match = USER_REGEX.search(line)
                if user_match:
                    user_id, username = int(user_match.group(1)), user_match.group(2).strip()
                    if user_id and username:
                        user_names[user_id] = username
    except Exception:
        pass  # Игнорируем ошибки, если лог-файл недоступен

    if not chat_titles and not user_names:
        return

    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            for chat_id, title in chat_titles.items():
                cursor.execute(
                    "UPDATE message_stats SET chat_title = ? WHERE chat_id = ? AND (chat_title IS NULL OR chat_title = '')",
                    (title, chat_id)
                )
            for user_id, username in user_names.items():
                cursor.execute(
                    "UPDATE message_stats SET user_username = ? WHERE user_id = ? AND (user_username IS NULL OR user_username = '')",
                    (username, user_id)
                )
            conn.commit()
    except Exception as e:
        logging.error(f"Не удалось обновить БД статистики: {e}", exc_info=True)

def init_db():
    """Инициализирует БД."""
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
        for column in ["chat_title", "user_name", "user_username"]:
            try:
                cursor.execute(f"ALTER TABLE message_stats ADD COLUMN {column} TEXT")
            except sqlite3.OperationalError:
                pass
        conn.commit()

async def log_activity(message: types.Message):
    """
    Универсальная функция для логирования активности, создающей нагрузку.
    """
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            is_private = message.chat.type == 'private'
            timestamp = datetime.now()
            cursor.execute(
                """INSERT INTO message_stats 
                   (chat_id, user_id, message_timestamp, message_type, is_private, chat_title, user_name, user_username) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.chat.id,
                    message.from_user.id,
                    timestamp,
                    message.content_type,
                    is_private,
                    message.chat.title if not is_private else None,
                    message.from_user.full_name,
                    message.from_user.username
                )
            )
            conn.commit()
    except Exception as e:
        logging.error(f"Error logging activity: {e}", exc_info=True)

def get_stats(period_hours: Optional[int] = None) -> Dict[str, Dict]:
    """
    Сначала обновляет данные из лога, затем получает статистику из БД.
    """
    _update_db_from_log()
    
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        
        params = []
        time_filter = ""
        if period_hours is not None:
            time_filter = "WHERE message_timestamp >= ?"
            params.append(datetime.now() - timedelta(hours=period_hours))

        # Статистика по групповым чатам (теперь здесь только команды)
        query_groups = f"""
            SELECT chat_id, MAX(chat_title), COUNT(*)
            FROM message_stats 
            WHERE is_private = 0 {time_filter.replace('WHERE', 'AND')}
            GROUP BY chat_id
        """
        cursor.execute(query_groups, params)
        group_stats = {
            (chat_title if chat_title else f"Чат ({chat_id})"): count
            for chat_id, chat_title, count in cursor.fetchall()
        }

        # Статистика по личным сообщениям (здесь все сообщения)
        query_private = f"""
            SELECT user_id, MAX(user_name), MAX(user_username), COUNT(*)
            FROM message_stats 
            WHERE is_private = 1 {time_filter.replace('WHERE', 'AND')}
            GROUP BY user_id
        """
        cursor.execute(query_private, params)
        private_stats = {}
        for user_id, user_name, user_username, count in cursor.fetchall():
            if user_name and user_name != user_username:
                display_name = f"{user_name} (@{user_username})" if user_username else user_name
            elif user_username:
                 display_name = f"@{user_username}"
            else:
                display_name = f"Пользователь ({user_id})"
            private_stats[display_name] = count

    return {"groups": group_stats, "private": private_stats}

async def get_total_messages() -> Dict[str, Dict]:
    return get_stats()

async def get_messages_last_24_hours() -> Dict[str, Dict]:
    return get_stats(period_hours=24)

async def get_messages_last_hour() -> Dict[str, Dict]:
    return get_stats(period_hours=1)
