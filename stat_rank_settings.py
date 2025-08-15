import os
import json
import logging
from datetime import date
from aiogram import types
from config import STATS_FILE, message_stats, bot
from prompts import RANKS

# Функция загрузки статистики
def load_stats():
    global message_stats
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as file:
                message_stats = json.load(file)
                logging.info(f"📊 Загружено {len(message_stats)} чатов в статистику.")
        except Exception as e:
            logging.error(f"Ошибка при загрузке статистики: {e}")
            message_stats = {}
    else:
        message_stats = {}

# Функция сохранения статистики
def save_stats():
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as file:
            json.dump(message_stats, file, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Ошибка при сохранении статистики: {e}")

# Загружаем статистику при запуске
load_stats()

# Функция для обновления статистики с сохранением предыдущих данных
async def track_message_statistics(message: types.Message):
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id)
    
    # Get current date
    current_date = date.today()
    current_date_str = current_date.isoformat()
    
    # Сохраняем предыдущее значение total, если оно есть
    previous_total = 0
    if chat_id in message_stats and user_id in message_stats[chat_id]:
        previous_total = message_stats[chat_id][user_id].get("total", 0)
    
    # Initialize stats structure if missing
    if chat_id not in message_stats:
        message_stats[chat_id] = {}
    if user_id not in message_stats[chat_id]:
        message_stats[chat_id][user_id] = {
            "total": previous_total,  # Используем предыдущее значение
            "daily": 0, 
            "weekly": 0,
            "last_daily_reset": current_date_str,
            "last_weekly_reset": current_date_str
        }
    
    user_stats = message_stats[chat_id][user_id]
    
    # Проверяем, что total не меньше предыдущего значения
    if user_stats["total"] < previous_total:
        user_stats["total"] = previous_total
    
    # Handle date conversion safely
    try:
        last_daily_reset = date.fromisoformat(user_stats.get("last_daily_reset", current_date_str))
    except (TypeError, ValueError):
        last_daily_reset = current_date
        user_stats["last_daily_reset"] = current_date_str
    
    try:
        last_weekly_reset = date.fromisoformat(user_stats.get("last_weekly_reset", current_date_str))
    except (TypeError, ValueError):
        last_weekly_reset = current_date
        user_stats["last_weekly_reset"] = current_date_str
    
    # Check and reset daily stats
    if current_date > last_daily_reset:
        user_stats["daily"] = 0
        user_stats["last_daily_reset"] = current_date_str
    
    # Check and reset weekly stats
    days_since_reset = (current_date - last_weekly_reset).days
    if days_since_reset >= 7:
        user_stats["weekly"] = 0
        user_stats["last_weekly_reset"] = current_date_str
    
    # Increment counters
    user_stats["total"] += 1
    user_stats["daily"] += 1
    user_stats["weekly"] += 1
    
    # Check for rank promotion
    new_rank = None
    for count, rank in sorted(RANKS.items()):
        if user_stats["total"] == count:
            new_rank = rank
            break
    
    if new_rank:
        await message.reply(f"🎉 Паздравляю, ты получил ранг **{new_rank}**!")
    
    # Save statistics
    save_stats()

async def get_user_statistics(chat_id: str, user_id: str) -> tuple[str, bool]:
    """
    Получает статистику пользователя.
    Возвращает кортеж (текст сообщения, флаг наличия статистики)
    """
    if chat_id not in message_stats or user_id not in message_stats[chat_id]:
        return "Ты пока ничего не написал, иди пиши.", False

    user_stats = message_stats[chat_id][user_id]

    user_rank = "без ранга"
    for count, rank in sorted(RANKS.items(), reverse=True):
        if user_stats["total"] >= count:
            user_rank = rank
            break

    response = (
        f"📊 **Твоя статистика:**\n"
        f"💬 Сегодня: {user_stats.get('daily', 0)} сообщений\n"
        f"📅 За неделю: {user_stats.get('weekly', 0)} сообщений\n"
        f"🌎 Всего: {user_stats.get('total', 0)} сообщений\n"
        f"🏅 Ранг: {user_rank}"
    )
    return response, True

# Вынесенная функция статистика чат
async def get_valid_users(chat_id: str) -> dict:
    """Получение списка валидных пользователей из статистики чата"""
    valid_users = {}
    if chat_id in message_stats:
        for user_id, stats in message_stats[chat_id].items():
            try:
                user_id_int = int(user_id)
                if user_id_int > 0:
                    valid_users[user_id] = stats
            except (ValueError, TypeError):
                logging.error(f"Некорректный user_id в статистике: {user_id}")
                continue
    return valid_users

async def get_user_display_name(chat_id: int, user_id: int) -> str:
    """Получение отображаемого имени пользователя"""
    try:
        chat_member = await bot.get_chat_member(chat_id, user_id)
        user = chat_member.user
        
        if user.first_name and user.last_name:
            return f"{user.first_name} {user.last_name}"
        elif user.first_name:
            return user.first_name
        elif user.username:
            return f"@{user.username}"
        return f"Пользователь {user_id}"
    except Exception as e:
        logging.error(f"Ошибка при получении информации о пользователе {user_id} в чате {chat_id}: {e}")
        return f"Пользователь {user_id}"

def get_user_rank(message_count: int) -> str:
    """Определение ранга пользователя по количеству сообщений"""
    for count, rank in sorted(RANKS.items(), reverse=True):
        if message_count >= count:
            return rank
    return "без ранга"

async def format_top_users(chat_id: str, valid_users: dict) -> tuple[list[str], int]:
    """Форматирование списка топ пользователей"""
    total_chat_messages = sum(stats.get("total", 0) for stats in valid_users.values())
    sorted_users = sorted(valid_users.items(), key=lambda x: x[1].get("total", 0), reverse=True)[:15]
    
    top_users = []
    for i, (user_id, stats) in enumerate(sorted_users, start=1):
        display_name = await get_user_display_name(int(chat_id), int(user_id))
        user_rank = get_user_rank(stats.get("total", 0))
        top_users.append(f"{i}. {display_name} - {stats.get('total', 0)} (<i>{user_rank}</i>)")
    
    return top_users, total_chat_messages

async def generate_chat_stats_report(chat_id: str) -> str | None:
    """
    Generates a complete report on chat statistics.
    Gathers all necessary data, formats it, and returns a ready-made string for the reply.
    Returns None if there is no data for the report.
    """
    logging.info(f"Запрос отчета по статистике для чата {chat_id}")
    
    valid_users = await get_valid_users(chat_id)
    if not valid_users:
        logging.warning(f"Для чата {chat_id} нет валидных пользователей в статистике.")
        return "В этом чате нет корректных статистических данных."

    top_users, total_chat_messages = await format_top_users(chat_id, valid_users)
    
    response = (
        "📊 <b>Топ хуяторов чата:</b>\n" +
        "\n".join(top_users) +
        f"\n\n💥 Весь чат нахуярил {total_chat_messages} сообщений, я фшоки!"
    )
    
    return response