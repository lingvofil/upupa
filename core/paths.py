"""Канонические пути к постоянным данным приложения.

Данные физически пока остаются в корне репозитория. Отличие в том, что путь
не зависит от текущей рабочей директории процесса.
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Пока не переносим данные в отдельный каталог: существующие production-файлы
# должны продолжать использоваться без отдельной миграции.
DATA_DIR = PROJECT_ROOT

CHAT_SETTINGS_PATH = DATA_DIR / "chat_settings.json"
USER_MESSAGES_LOG_PATH = DATA_DIR / "user_messages.log"
MESSAGE_STATS_PATH = DATA_DIR / "message_stats.json"
CHAT_LIST_PATH = DATA_DIR / "chats.json"
SMS_DISABLED_CHATS_PATH = DATA_DIR / "sms_disabled_chats.json"
STATISTICS_DB_PATH = DATA_DIR / "statistics.db"
WORLD_DB_PATH = DATA_DIR / "world.db"
ANTISPAM_SETTINGS_PATH = DATA_DIR / "antispam_enabled.json"
RANK_NOTIFICATIONS_PATH = DATA_DIR / "rank_notifications_settings.json"
DND_STATE_PATH = DATA_DIR / "dnd_sessions.json"
CROCODILE_STATE_PATH = DATA_DIR / "crocodile_sessions.json"
