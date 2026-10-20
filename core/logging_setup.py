"""Настройка логирования. Импортировать раньше остальных core-модулей."""
import logging
import re
from logging.handlers import RotatingFileHandler

from core.paths import PROJECT_ROOT


BOT_LOG_PATH = PROJECT_ROOT / "bot_log.txt"
BOT_LOG_MAX_BYTES = 25 * 1024 * 1024
BOT_LOG_BACKUP_COUNT = 4
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

# Telegram-токен: в URL Bot API (".../bot123:ABC/...") и сам по себе.
_TELEGRAM_TOKEN_IN_URL_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{30,}")
# Ключи в query string: Giphy (api_key), Google API (key) и подобные.
_SECRET_QUERY_PARAM_RE = re.compile(
    r"([?&](?:api_key|apikey|key|token|access_token)=)[^&#\s'\"]+",
    re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    """Маскирует токены и ключи, которые могут попасть в лог вместе с URL."""
    text = _TELEGRAM_TOKEN_IN_URL_RE.sub("bot<REDACTED>", text)
    text = _TELEGRAM_TOKEN_RE.sub("<REDACTED>", text)
    return _SECRET_QUERY_PARAM_RE.sub(r"\1<REDACTED>", text)


class SecretRedactingFormatter(logging.Formatter):
    """Маскирует секреты в готовой строке: сообщении, аргументах и traceback."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


_file_handler = RotatingFileHandler(
    BOT_LOG_PATH,
    maxBytes=BOT_LOG_MAX_BYTES,
    backupCount=BOT_LOG_BACKUP_COUNT,
    encoding="utf-8",
    delay=True,
)
_file_handler.setFormatter(SecretRedactingFormatter(LOG_FORMAT))

logging.basicConfig(
    level=logging.INFO,
    handlers=[_file_handler],
    format=LOG_FORMAT,
)

logger = logging.getLogger(__name__)
logging.getLogger("aiogram").setLevel(logging.WARNING)
logging.getLogger("aiogram.dispatcher").setLevel(logging.WARNING)
logging.getLogger("aiogram.event").setLevel(logging.WARNING)
# httpx пишет полный URL каждого запроса на INFO, включая токены в пути и ключи в query.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
