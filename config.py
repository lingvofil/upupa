# =========================
# === config.py ===
# =========================

import os
import time
import random
import logging
from typing import List, Dict, Optional

import google.generativeai as genai
from google.api_core import exceptions

from aiogram import Bot, Dispatcher, Router
from gigachat import GigaChat

# =========================
# === ИМПОРТ СЕКРЕТОВ ===
# =========================
try:
    from config_private import (
        API_TOKEN,
        GENERIC_API_KEY,
        GENERIC_API_KEY2,
        GENERIC_API_KEY3,
        GENERIC_API_KEY4,
        GENERIC_API_KEY5,
        GENERIC_API_KEY6,
        OPENROUTER_API_KEY,
        GOOGLE_API_KEY,
        GOOGLE_API_KEY2,
        giphy_api_key,
        KANDINSKY_API_KEY,
        KANDINSKY_SECRET_KEY,
        GIGACHAT_API_KEY,
        GIGACHAT_CLIENT_ID,
        CLOUDFLARE_ACCOUNT_ID,
        CLOUDFLARE_API_TOKEN,
        HUGGINGFACE_TOKEN
    )
except ImportError:
    API_TOKEN = os.getenv("API_TOKEN")
    GENERIC_API_KEY = os.getenv("GENERIC_API_KEY")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# =========================
# === GEMINI KEYS ===
# =========================
GEMINI_KEYS_POOL = [
    key for key in [
        GENERIC_API_KEY,
        GENERIC_API_KEY2,
        GENERIC_API_KEY3,
        GENERIC_API_KEY4,
        GENERIC_API_KEY5,
        GENERIC_API_KEY6,
        GOOGLE_API_KEY,
        GOOGLE_API_KEY2,
    ]
    if key
]

if not GEMINI_KEYS_POOL:
    raise RuntimeError("❌ Gemini API keys not found")

# Используем ОДИН ключ (лимиты аккаунтные)
PRIMARY_GEMINI_KEY = GEMINI_KEYS_POOL[0]
genai.configure(api_key=PRIMARY_GEMINI_KEY)

# =========================
# === RATE LIMIT CONTROL ===
# =========================
GLOBAL_MIN_DELAY = 2.5          # безопасный интервал
GEMINI_ACCOUNT_COOLDOWN = 300   # 5 минут

_last_call_ts = 0.0
_gemini_blocked_until = 0.0


def gemini_available() -> bool:
    return time.time() >= _gemini_blocked_until


def mark_gemini_blocked():
    global _gemini_blocked_until
    _gemini_blocked_until = time.time() + GEMINI_ACCOUNT_COOLDOWN
    logging.warning("⛔ Gemini API temporarily blocked (account cooldown)")


def global_throttle():
    global _last_call_ts
    now = time.time()
    delta = now - _last_call_ts
    if delta < GLOBAL_MIN_DELAY:
        time.sleep(GLOBAL_MIN_DELAY - delta)
    _last_call_ts = time.time()

# =========================
# === ПРОЧИЕ КОНСТАНТЫ ===
# =========================
BLOCKED_USERS = [354145389]
ADMIN_ID = 126386976
SPECIAL_CHAT_ID = -1001707530786

SEARCH_ENGINE_ID = "33026288e406447ea"

# =========================
# === GEMINI MODEL QUEUES ===
# =========================
MODEL_QUEUE_DEFAULT = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

MODEL_QUEUE_SPECIAL = [
    "gemini-2.5-pro",
] + MODEL_QUEUE_DEFAULT

# =========================
# === GEMINI FALLBACK WRAPPER ===
# =========================
class GeminiWrapper:
    def __init__(self, default_queue, special_queue):
        self.default_queue = default_queue
        self.special_queue = special_queue
        self.last_used_model_name: Optional[str] = None

    def _queue(self, chat_id: Optional[int]):
        if chat_id and str(chat_id) == str(SPECIAL_CHAT_ID):
            return self.special_queue
        return self.default_queue

    def generate_content(
        self,
        prompt,
        *,
        chat_id: Optional[int] = None,
        **kwargs
    ):
        if not gemini_available():
            raise RuntimeError("Gemini temporarily unavailable")

        for model_name in self._queue(chat_id):
            try:
                global_throttle()
                model = genai.GenerativeModel(model_name)
                result = model.generate_content(prompt, **kwargs)
                self.last_used_model_name = model_name
                return result

            except exceptions.ResourceExhausted:
                mark_gemini_blocked()
                raise

            except Exception as e:
                logging.error(f"Gemini error [{model_name}]: {e}")

        raise RuntimeError("All Gemini models failed")

    def start_chat(
        self,
        history=None,
        *,
        chat_id: Optional[int] = None
    ):
        if not gemini_available():
            raise RuntimeError("Gemini temporarily unavailable")

        model_name = self._queue(chat_id)[0]
        model = genai.GenerativeModel(model_name)
        chat = model.start_chat(history=history or [])
        self.last_used_model_name = model_name
        return chat


# =========================
# === ИНИЦИАЛИЗАЦИЯ ===
# =========================
gemini_model = GeminiWrapper(
    MODEL_QUEUE_DEFAULT,
    MODEL_QUEUE_SPECIAL
)

# =========================
# === GIGACHAT ===
# =========================
gigachat = GigaChat(
    credentials=GIGACHAT_API_KEY,
    model="GigaChat-2",
    verify_ssl_certs=False
)

# =========================
# === AIROGRAM ===
# =========================
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()

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

MAX_HISTORY_LENGTH = 20
DIALOG_ENABLED = True

# =========================
# === LOGGING ===
# =========================
logging.basicConfig(
    level=logging.INFO,
    filename="bot_log.txt",
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)
