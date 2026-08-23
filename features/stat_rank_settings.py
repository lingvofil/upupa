import asyncio
import copy
import logging
from datetime import date

from aiogram import types

from core.json_repository import JsonFileRepository, JsonRepository
from core.loader import bot
from core.paths import MESSAGE_STATS_PATH, RANK_NOTIFICATIONS_PATH
from core.state import message_stats
from prompts import RANKS

# Множество чатов, где уведомления о рангах ОТКЛЮЧЕНЫ.
# Identity сохраняем: другие модули могут держать ссылку на этот set.
rank_notifications_disabled_chats = set()

# Один update/save за раз в production event loop. Lock пересоздаётся, если модуль
# используется из другого loop (например, в нескольких asyncio.run тестах).
_stats_update_lock: asyncio.Lock | None = None
_stats_update_loop = None


def _get_stats_update_lock() -> asyncio.Lock:
    global _stats_update_lock, _stats_update_loop
    loop = asyncio.get_running_loop()
    if _stats_update_lock is None or _stats_update_loop is not loop:
        _stats_update_lock = asyncio.Lock()
        _stats_update_loop = loop
    return _stats_update_lock


def _rank_notifications_repository() -> JsonFileRepository:
    return JsonFileRepository(RANK_NOTIFICATIONS_PATH)


def _stats_repository() -> JsonFileRepository:
    return JsonFileRepository(MESSAGE_STATS_PATH)


def load_rank_notifications_settings(repository: JsonRepository | None = None):
    """Загрузить настройки уведомлений о рангах без замены shared set."""
    repo = repository or _rank_notifications_repository()
    try:
        data = repo.load()
    except FileNotFoundError:
        rank_notifications_disabled_chats.clear()
        return
    except Exception as e:
        logging.error(f"Ошибка при загрузке настроек уведомлений о рангах: {e}")
        rank_notifications_disabled_chats.clear()
        return

    disabled_chats = data.get("disabled_chats", []) if isinstance(data, dict) else []
    if isinstance(disabled_chats, list):
        rank_notifications_disabled_chats.clear()
        rank_notifications_disabled_chats.update(disabled_chats)
        logging.info(
            f"🔕 Загружены настройки уведомлений о рангах для "
            f"{len(rank_notifications_disabled_chats)} чатов."
        )
    else:
        rank_notifications_disabled_chats.clear()
        logging.warning("Файл настроек уведомлений о рангах повреждён; используется пустой set.")


def save_rank_notifications_settings(repository: JsonRepository | None = None):
    """Атомарно сохранить настройки уведомлений о рангах."""
    repo = repository or _rank_notifications_repository()
    try:
        repo.save({"disabled_chats": list(rank_notifications_disabled_chats)})
        logging.info("💾 Настройки уведомлений о рангах сохранены.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении настроек уведомлений о рангах: {e}")


def load_stats(repository: JsonRepository | None = None):
    """Загрузить message_stats, сохраняя identity shared dict."""
    repo = repository or _stats_repository()
    try:
        data = repo.load()
    except FileNotFoundError:
        message_stats.clear()
        return
    except Exception as e:
        logging.error(f"Ошибка при загрузке статистики: {e}")
        message_stats.clear()
        return

    if isinstance(data, dict):
        message_stats.clear()
        message_stats.update(data)
        logging.info(f"📊 Загружено {len(message_stats)} чатов в статистику.")
    else:
        message_stats.clear()
        logging.warning("Файл статистики повреждён; используется пустой словарь.")


def save_stats(repository: JsonRepository | None = None, *, value=None):
    """Атомарно сохранить message_stats или переданный immutable snapshot."""
    repo = repository or _stats_repository()
    payload = message_stats if value is None else value
    try:
        repo.save(payload)
    except Exception as e:
        logging.error(f"Ошибка при сохранении статистики: {e}")


def load_stat_rank_state() -> None:
    """Явно загрузить JSON-состояние рангов и счётчиков на startup."""
    load_rank_notifications_settings()
    load_stats()


async def track_message_statistics(message: types.Message):
    # В отличие от простого ``to_thread(save_stats)``, lock + snapshot не дают
    # более старой записи завершиться после новой и затереть свежий счётчик.
    async with _get_stats_update_lock():
        chat_id = str(message.chat.id)
        user_id = str(message.from_user.id)

        current_date = date.today()
        current_date_str = current_date.isoformat()

        previous_total = 0
        if chat_id in message_stats and user_id in message_stats[chat_id]:
            previous_total = message_stats[chat_id][user_id].get("total", 0)

        if chat_id not in message_stats:
            message_stats[chat_id] = {}
        if user_id not in message_stats[chat_id]:
            message_stats[chat_id][user_id] = {
                "total": previous_total,
                "daily": 0,
                "weekly": 0,
                "last_daily_reset": current_date_str,
                "last_weekly_reset": current_date_str,
            }

        user_stats = message_stats[chat_id][user_id]

        if user_stats.get("total", 0) < previous_total:
            user_stats["total"] = previous_total

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

        if current_date > last_daily_reset:
            user_stats["daily"] = 0
            user_stats["last_daily_reset"] = current_date_str

        days_since_reset = (current_date - last_weekly_reset).days
        if days_since_reset >= 7:
            user_stats["weekly"] = 0
            user_stats["last_weekly_reset"] = current_date_str

        user_stats["total"] = user_stats.get("total", 0) + 1
        user_stats["daily"] = user_stats.get("daily", 0) + 1
        user_stats["weekly"] = user_stats.get("weekly", 0) + 1

        new_rank = None
        for count, rank in sorted(RANKS.items()):
            if user_stats["total"] == count:
                new_rank = rank
                break

        if new_rank and chat_id not in rank_notifications_disabled_chats:
            await message.reply(f"🎉 Паздравляю, ты получил ранг **{new_rank}**!")

        snapshot = copy.deepcopy(message_stats)
        await asyncio.to_thread(save_stats, value=snapshot)


async def get_user_statistics(chat_id: str, user_id: str) -> tuple[str, bool]:
    """Получает статистику пользователя."""
    if chat_id not in message_stats or user_id not in message_stats[chat_id]:
        return "Ты пока ничего не написал, иди пиши.", False

    user_stats = message_stats[chat_id][user_id]

    user_rank = "без ранга"
    for count, rank in sorted(RANKS.items(), reverse=True):
        if user_stats.get("total", 0) >= count:
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


async def get_valid_users(chat_id: str) -> dict:
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
    for count, rank in sorted(RANKS.items(), reverse=True):
        if message_count >= count:
            return rank
    return "без ранга"


async def format_top_users(chat_id: str, valid_users: dict) -> tuple[list[str], int]:
    total_chat_messages = sum(stats.get("total", 0) for stats in valid_users.values())
    sorted_users = sorted(valid_users.items(), key=lambda x: x[1].get("total", 0), reverse=True)[:15]

    top_users = []
    for i, (user_id, stats) in enumerate(sorted_users, start=1):
        display_name = await get_user_display_name(int(chat_id), int(user_id))
        user_rank = get_user_rank(stats.get("total", 0))
        top_users.append(f"{i}. {display_name} - {stats.get('total', 0)} (<i>{user_rank}</i>)")

    return top_users, total_chat_messages


async def generate_chat_stats_report(chat_id: str) -> str | None:
    logging.info(f"Запрос отчета по статистике для чата {chat_id}")

    valid_users = await get_valid_users(chat_id)
    if not valid_users:
        logging.warning(f"Для чата {chat_id} нет валидных пользователей в статистике.")
        return "В этом чате нет корректных статистических данных."

    top_users, total_chat_messages = await format_top_users(chat_id, valid_users)

    return (
        "📊 <b>Топ хуяторов чата:</b>\n"
        + "\n".join(top_users)
        + f"\n\n💥 Весь чат нахуярил {total_chat_messages} сообщений, я фшоки!"
    )
