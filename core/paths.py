"""Канонические пути к постоянным данным приложения.

На этапе R2 данные физически остаются в корне репозитория, как и раньше.
Отличие в том, что путь больше не зависит от текущей рабочей директории процесса.
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Пока не переносим данные в отдельный каталог, чтобы R2 не требовал миграции
# продовых json/db/log-файлов. Позже DATA_DIR можно будет переключить отдельно.
DATA_DIR = PROJECT_ROOT

CHAT_SETTINGS_PATH = DATA_DIR / "chat_settings.json"
USER_MESSAGES_LOG_PATH = DATA_DIR / "user_messages.log"
MESSAGE_STATS_PATH = DATA_DIR / "message_stats.json"
CHAT_LIST_PATH = DATA_DIR / "chats.json"
SMS_DISABLED_CHATS_PATH = DATA_DIR / "sms_disabled_chats.json"
STATISTICS_DB_PATH = DATA_DIR / "statistics.db"
ANTISPAM_SETTINGS_PATH = DATA_DIR / "antispam_enabled.json"
