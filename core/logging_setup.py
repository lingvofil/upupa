"""Настройка логирования. Импортировать раньше остальных core-модулей."""
import logging
from logging.handlers import RotatingFileHandler

from core.paths import PROJECT_ROOT


BOT_LOG_PATH = PROJECT_ROOT / "bot_log.txt"
BOT_LOG_MAX_BYTES = 25 * 1024 * 1024
BOT_LOG_BACKUP_COUNT = 4

_file_handler = RotatingFileHandler(
    BOT_LOG_PATH,
    maxBytes=BOT_LOG_MAX_BYTES,
    backupCount=BOT_LOG_BACKUP_COUNT,
    encoding="utf-8",
    delay=True,
)

logging.basicConfig(
    level=logging.INFO,
    handlers=[_file_handler],
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)
logging.getLogger("aiogram").setLevel(logging.WARNING)
logging.getLogger("aiogram.dispatcher").setLevel(logging.WARNING)
logging.getLogger("aiogram.event").setLevel(logging.WARNING)