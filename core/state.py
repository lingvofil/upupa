"""Рантайм-состояние бота: словари, множества, пути к файлам данных.

Все модули мутируют ЭТИ объекты по ссылке — не пересоздавать!
"""
from datetime import datetime, timedelta

from core.logging_setup import logger

# =========================
# === FILES / STATE ===
# =========================
CHAT_SETTINGS_FILE = "chat_settings.json"
LOG_FILE = "user_messages.log"
STATS_FILE = "message_stats.json"
CHAT_LIST_FILE = "chats.json"
SMS_DISABLED_CHATS_FILE = "sms_disabled_chats.json"
DB_FILE = "statistics.db"

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

