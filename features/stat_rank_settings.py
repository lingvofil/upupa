import asyncio
import logging
from datetime import date

from aiogram import types

from core.json_repository import JsonFileRepository, JsonRepository
from core.loader import bot
from core.paths import MESSAGE_STATS_PATH, RANK_NOTIFICATIONS_PATH
from infrastructure.persistence.sqlite_rank_counters import SQLiteRankCountersRepository
from prompts import RANKS

# Множество чатов, где уведомления о рангах ОТКЛЮЧЕНЫ.
# Identity сохраняем: другие модули могут держать ссылку на этот set.
rank_notifications_disabled_chats = set()

_counter_repository: SQLiteRankCountersRepository | None = None


def configure_counter_repository(repository: SQLiteRankCountersRepository) -> None:
    global _counter_repository
    _counter_repository = repository


def _counters() -> SQLiteRankCountersRepository:
    if _counter_repository is None:
        raise RuntimeError("Rank counters have not been initialized")
    return _counter_repository


def _rank_notifications_repository() -> JsonFileRepository:
    return JsonFileRepository(RANK_NOTIFICATIONS_PATH)


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


def load_stat_rank_state() -> None:
    """Load notification settings and migrate counters before polling starts."""
    load_rank_notifications_settings()
    _counters().initialize(MESSAGE_STATS_PATH)


async def track_message_statistics(message: types.Message):
    if not message.from_user or message.from_user.is_bot:
        return
    chat_id = str(message.chat.id)
    stats = await asyncio.to_thread(
        _counters().increment, chat_id, str(message.from_user.id), date.today()
    )
    # The transaction is committed before Telegram I/O; a failed reply cannot
    # lose the increment or block counters in other chats.
    new_rank = RANKS.get(stats["total"])
    if new_rank and chat_id not in rank_notifications_disabled_chats:
        try:
            await message.reply(f"🎉 Паздравляю, ты получил ранг **{new_rank}**!")
        except Exception:
            logging.exception("Failed to send rank notification")


async def get_user_statistics(chat_id: str, user_id: str) -> tuple[str, bool]:
    """Получает статистику пользователя."""
    user_stats = await asyncio.to_thread(_counters().get_user, chat_id, user_id, date.today())
    if user_stats is None:
        return "Ты пока ничего не написал, иди пиши.", False

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
    users = await asyncio.to_thread(_counters().get_chat, chat_id, date.today())
    valid_users = {}
    for user_id, stats in users.items():
        try:
            if int(user_id) > 0:
                valid_users[user_id] = stats
        except (ValueError, TypeError):
            logging.error("Некорректный user_id в статистике: %s", user_id)
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
