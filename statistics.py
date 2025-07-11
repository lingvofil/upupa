# statistics.py
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, Awaitable

# --- НОВЫЕ ИМПОРТЫ ДЛЯ MIDDLEWARE ---
from aiogram import BaseMiddleware
from aiogram.types import Message
# ------------------------------------

# Убедитесь, что в Config.py добавлена переменная DB_FILE и ADMIN_ID
from config import DB_FILE, ADMIN_ID

# --- НОВЫЙ БЛОК: MIDDLEWARE ДЛЯ ОГРАНИЧЕНИЯ В ЛС ---

# Словарь для отслеживания времени последнего сообщения от пользователя в ЛС
private_message_timestamps: Dict[int, datetime] = {}
PRIVATE_MESSAGE_COOLDOWN = timedelta(hours=1)

class PrivateRateLimitMiddleware(BaseMiddleware):
    """
    Middleware для ограничения частоты сообщений в личных чатах.
    Проверяет каждое сообщение. Если оно в ЛС, не от админа и слишком частое,
    то отправляет предупреждение и блокирует дальнейшую обработку.
    """
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        # Проверяем, что это личный чат и сообщение от пользователя
        if event.chat.type == 'private' and event.from_user:
            user_id = event.from_user.id
            
            # Админа не ограничиваем
            if user_id == ADMIN_ID:
                return await handler(event, data)

            now = datetime.now()
            last_message_time = private_message_timestamps.get(user_id)

            if last_message_time:
                # Если с момента последнего сообщения прошло меньше часа
                if now - last_message_time < PRIVATE_MESSAGE_COOLDOWN:
                    # Отвечаем и прерываем дальнейшую обработку
                    await event.reply("иди общайся в чат, хитрый педераст")
                    return
            
            # Если ограничение не сработало, обновляем время последнего сообщения для этого пользователя
            private_message_timestamps[user_id] = now

        # Если это не личный чат или ограничение не сработало, пропускаем сообщение дальше
        return await handler(event, data)

# --- Инициализация Базы Данных ---

def init_db():
    """Инициализирует БД и обновляет схему таблицы, если это необходимо."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Создаем таблицу, если она не существует
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
    
    # Добавляем новые столбцы, если они отсутствуют, для обратной совместимости
    try:
        cursor.execute("ALTER TABLE message_stats ADD COLUMN chat_title TEXT")
    except sqlite3.OperationalError:
        pass  # Столбец уже существует
    try:
        cursor.execute("ALTER TABLE message_stats ADD COLUMN user_name TEXT")
    except sqlite3.OperationalError:
        pass  # Столбец уже существует
    try:
        cursor.execute("ALTER TABLE message_stats ADD COLUMN user_username TEXT")
    except sqlite3.OperationalError:
        pass  # Столбец уже существует

    conn.commit()
    conn.close()

# --- Логирование Сообщений ---

async def log_message(chat_id: int, user_id: int, message_type: str, is_private: bool,
                      chat_title: Optional[str], user_name: str, user_username: Optional[str]):
    """Записывает информацию о сообщении в базу данных."""
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
        # Здесь рекомендуется использовать ваш основной логгер из проекта
        print(f"Error logging message: {e}")

# --- Получение Статистики ---

def get_stats(period_hours: Optional[int] = None) -> Dict[str, Dict]:
    """
    Получает статистику сообщений за указанный период.

    Args:
        period_hours: Период в часах (24 - сутки, 1 - час). Если None - за всё время.

    Returns:
        Словарь со статистикой по группам и личным сообщениям.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    params = []
    time_filter = ""
    if period_hours is not None:
        time_filter = "WHERE message_timestamp >= ?"
        params.append(datetime.now() - timedelta(hours=period_hours))

    # Статистика по групповым чатам
    query_groups = f"""
        SELECT 
            COALESCE(chat_title, chat_id), 
            COUNT(*) 
        FROM message_stats 
        WHERE is_private = 0 
        {'AND message_timestamp >= ?' if period_hours is not None else ''}
        GROUP BY COALESCE(chat_title, chat_id)
        ORDER BY COUNT(*) DESC
    """
    cursor.execute(query_groups, params if period_hours is not None else [])
    group_stats = {str(row[0]): row[1] for row in cursor.fetchall()}

    # Статистика по личным сообщениям
    query_private = f"""
        SELECT 
            user_name, 
            user_username, 
            COUNT(*) 
        FROM message_stats 
        WHERE is_private = 1 
        {'AND message_timestamp >= ?' if period_hours is not None else ''}
        GROUP BY user_id, user_name, user_username 
        ORDER BY COUNT(*) DESC
    """
    cursor.execute(query_private, params if period_hours is not None else [])
    private_stats = {}
    for row in cursor.fetchall():
        user_name, user_username, count = row
        display_name = f"{user_name} (@{user_username})" if user_username else user_name
        private_stats[display_name] = count

    conn.close()
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
    """
    Возвращает активность по часам.
    
    Args:
        period_hours: Период в часах. Если None - за всё время.
    
    Returns:
        Словарь {час: количество_сообщений}.
    """
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
    data = {hour: 0 for hour in range(24)} # Заполняем все часы нулями для полноты
    for row in cursor.fetchall():
        data[int(row[0])] = row[1]
        
    conn.close()
    return data
