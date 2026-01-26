# === config.py ===

import os
import time
import random
import logging
from typing import List, Dict, Optional, Any
from groq import Groq
from PIL import Image
import base64
import io
import google.generativeai as genai
from google.api_core import exceptions
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, Router
from gigachat import GigaChat

# Импорт GroqWrapper из отдельного модуля
from groq_wrapper import GroqWrapper

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
        HUGGINGFACE_TOKEN,
        GROQ_API_KEY,
        POLLINATIONS_API_KEY
    )
except ImportError:
    API_TOKEN = os.getenv("API_TOKEN")
    GENERIC_API_KEY = os.getenv("GENERIC_API_KEY")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY")

# Инициализация
try:
    from config_private import GROQ_API_KEY
except ImportError:
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")

groq_ai = GroqWrapper(GROQ_API_KEY)

# =========================
# === GEMINI KEYS ===
# =========================
GEMINI_KEYS_POOL = [
    key for key in [
        #GENERIC_API_KEY,
        GENERIC_API_KEY2,
        #GENERIC_API_KEY3,
        #GENERIC_API_KEY4,
        #GENERIC_API_KEY5,
        #GENERIC_API_KEY6,
    ]
    if key
]

if not GEMINI_KEYS_POOL:
    raise RuntimeError("⚠ Gemini API keys not found")

PRIMARY_GEMINI_KEY = GEMINI_KEYS_POOL[0]
genai.configure(api_key=PRIMARY_GEMINI_KEY)

# =========================
# === RATE LIMIT CONTROL ===
# =========================
GLOBAL_MIN_DELAY = 2.5
GEMINI_ACCOUNT_COOLDOWN = 300

_last_call_ts = 0.0
_gemini_blocked_until = 0.0


def _global_throttle():
    global _last_call_ts
    now = time.time()
    delta = now - _last_call_ts
    if delta < GLOBAL_MIN_DELAY:
        time.sleep(GLOBAL_MIN_DELAY - delta)
    _last_call_ts = time.time()


def _gemini_available() -> bool:
    return time.time() >= _gemini_blocked_until


def _block_gemini():
    global _gemini_blocked_until
    _gemini_blocked_until = time.time() + GEMINI_ACCOUNT_COOLDOWN
    logging.warning("⛔ Gemini account cooldown activated")


# =========================
# === ПРОЧИЕ КОНСТАНТЫ ===
# =========================
SEARCH_ENGINE_ID = "33026288e406447ea"

BLOCKED_USERS = [354145389]
ADMIN_ID = 126386976
SPECIAL_CHAT_ID = -1001707530786

# =========================
# === MODEL QUEUES ===
# =========================
MODEL_QUEUE_DEFAULT = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

MODEL_QUEUE_SPECIAL = [
    "gemini-2.5-pro",
] + MODEL_QUEUE_DEFAULT

# =========================
# === GIGACHAT MODEL QUEUES ===
# =========================
GIGACHAT_MODEL_QUEUE_DEFAULT = ["GigaChat-2"]
GIGACHAT_MODEL_QUEUE_SPECIAL = ["GigaChat-2-Max"]

# =========================
# === PUBLIC MODEL CONSTANTS (ВОССТАНОВЛЕНЫ) ===
# =========================
TEXT_GENERATION_MODEL_LIGHT = "gemini-2.0-flash-lite-preview-02-05"
ROBOTICS_MODEL = "gemini-robotics-er-1.5-preview"

TTS_MODELS_QUEUE = [
    "gemini-2.5-flash-preview-tts"
]

# =========================
# === STATIC MODELS (ВОССТАНОВЛЕНЫ) ===
# =========================
search_model = genai.GenerativeModel("gemini-2.5-flash")
image_model = genai.GenerativeModel("imagen-3.0-generate-001")
edit_model = genai.GenerativeModel(
    "models/gemini-2.0-flash-preview-image-generation"
)

# =========================
# === FALLBACK CHAT SESSION ===
# =========================
class FallbackChatSession:
    def __init__(
        self,
        wrapper,
        history: Optional[List[Any]] = None,
        model_queue: Optional[List[str]] = None,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None
    ):
        self.wrapper = wrapper
        self.history = history or []
        self.model_queue = model_queue or wrapper.default_queue
        self.chat_id = chat_id
        self.user_id = user_id

    def send_message(self, content):
        if not _gemini_available():
            raise RuntimeError("Gemini temporarily unavailable")

        for model_name in self.model_queue:
            try:
                _global_throttle()
                model = genai.GenerativeModel(model_name)
                chat = model.start_chat(history=self.history)
                response = chat.send_message(content)
                self.history = chat.history
                self.wrapper.last_used_model_name = model_name
                return response

            except exceptions.ResourceExhausted:
                _block_gemini()
                raise

            except Exception as e:
                logging.error(f"Chat error [{model_name}]: {e}")

        raise RuntimeError("All Gemini chat models failed")


# =========================
# === MODEL FALLBACK WRAPPER ===
# =========================
class ModelFallbackWrapper:
    def __init__(self, default_queue: List[str], special_queue: List[str]):
        self.default_queue = default_queue
        self.special_queue = special_queue
        self.last_used_model_name: Optional[str] = None

    def _get_queue(self, chat_id: Optional[int]):
        if chat_id and str(chat_id) == str(SPECIAL_CHAT_ID):
            return self.special_queue
        return self.default_queue

    def generate_content(self, prompt, *, chat_id=None, **kwargs):
        if not _gemini_available():
            raise RuntimeError("Gemini temporarily unavailable")

        for model_name in self._get_queue(chat_id):
            try:
                _global_throttle()
                model = genai.GenerativeModel(model_name)
                result = model.generate_content(prompt, **kwargs)
                self.last_used_model_name = model_name
                return result

            except exceptions.ResourceExhausted:
                _block_gemini()
                raise

            except Exception as e:
                logging.error(f"Generate error [{model_name}]: {e}")

        raise RuntimeError("All Gemini models failed")

    def generate_custom(self, model_name: str, *args, **kwargs):
        if not _gemini_available():
            raise RuntimeError("Gemini temporarily unavailable")

        try:
            _global_throttle()
            model = genai.GenerativeModel(model_name)
            return model.generate_content(*args, **kwargs)

        except exceptions.ResourceExhausted:
            _block_gemini()
            raise

    def start_chat(self, history=None, chat_id=None, user_id=None):
        queue = self._get_queue(chat_id)
        return FallbackChatSession(
            self,
            history=history,
            model_queue=queue,
            chat_id=chat_id,
            user_id=user_id
        )

    @property
    def model_names(self):
        return self.default_queue


# =========================
# === GIGACHAT WRAPPER ===
# =========================
class GigaChatWrapper:
    def __init__(self, api_key: str, default_queue: List[str], special_queue: List[str]):
        self.api_key = api_key
        self.default_queue = default_queue
        self.special_queue = special_queue
        self.last_used_model_name: Optional[str] = None

    def _get_queue(self, chat_id: Optional[int]):
        if chat_id and str(chat_id) == str(SPECIAL_CHAT_ID):
            return self.special_queue
        return self.default_queue

    def generate_content(self, prompt: str, *, chat_id=None):
        """Генерация ответа с помощью GigaChat"""
        queue = self._get_queue(chat_id)
        
        for model_name in queue:
            try:
                with GigaChat(
                    credentials=self.api_key,
                    verify_ssl_certs=False,
                    temperature=0.7,
                    max_tokens=500,
                    model=model_name
                ) as giga:
                    response = giga.chat(prompt)
                    self.last_used_model_name = model_name
                    
                    # Создаём объект-обёртку для совместимости с Gemini API
                    class GigaResponse:
                        def __init__(self, text):
                            self.text = text
                    
                    return GigaResponse(response.choices[0].message.content)
                    
            except Exception as e:
                logging.error(f"GigaChat error [{model_name}]: {e}")
                continue
        
        raise RuntimeError("All GigaChat models failed")


# =========================
# === PUBLIC CONTRACT ===
# =========================
model = ModelFallbackWrapper(
    MODEL_QUEUE_DEFAULT,
    MODEL_QUEUE_SPECIAL
)

gigachat_model = GigaChatWrapper(
    GIGACHAT_API_KEY,
    GIGACHAT_MODEL_QUEUE_DEFAULT,
    GIGACHAT_MODEL_QUEUE_SPECIAL
)

# =========================
# === GIGACHAT (старый объект для совместимости) ===
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
