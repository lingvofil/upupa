"""Рантайм-состояние бота: словари, множества и compatibility-пути.

Все модули мутируют ЭТИ объекты по ссылке — не пересоздавать!
"""
from datetime import datetime, timedelta

from core.logging_setup import logger
from core.paths import (
    CHAT_LIST_PATH,
    CHAT_SETTINGS_PATH,
    MESSAGE_STATS_PATH,
    SMS_DISABLED_CHATS_PATH,
    STATISTICS_DB_PATH,
    USER_MESSAGES_LOG_PATH,
)

# =========================
# === FILES / STATE ===
# =========================
# Legacy-имена оставлены строками, чтобы существующие импорты через config.py
# не меняли тип. Источник истины для путей теперь core.paths.
CHAT_SETTINGS_FILE = str(CHAT_SETTINGS_PATH)
LOG_FILE = str(USER_MESSAGES_LOG_PATH)
STATS_FILE = str(MESSAGE_STATS_PATH)
CHAT_LIST_FILE = str(CHAT_LIST_PATH)
SMS_DISABLED_CHATS_FILE = str(SMS_DISABLED_CHATS_PATH)
DB_FILE = str(STATISTICS_DB_PATH)

chat_settings = {}
conversation_history = {}
message_stats = {}
quiz_questions = {}
quiz_states = {}
chat_list = []
sms_disabled_chats = set()
ANTISPAM_ENABLED_CHATS = set()

serious_mode_messages = {}


def cleanup_old_serious_messages():
    """Очистка записей старше 24 часов"""
    cutoff = datetime.now() - timedelta(hours=24)
    to_remove = [
        msg_id for msg_id, data in serious_mode_messages.items()
        if isinstance(data, dict) and data.get('timestamp', datetime.now()) < cutoff
    ]
    for msg_id in to_remove:
        del serious_mode_messages[msg_id]

    if to_remove:
        logger.info(f"Очищено {len(to_remove)} старых записей серьёзного режима")


MAX_HISTORY_LENGTH = 20
DIALOG_ENABLED = True
