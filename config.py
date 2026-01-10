# =========================
# config.py (FULL, FIXED)
# =========================

import os
import time
import json
import random
import logging
from typing import List, Dict, Optional, Any

from aiogram import Bot, Dispatcher, Router

import google.generativeai as genai
from google.api_core import exceptions

from gigachat import GigaChat
import requests

# =========================
# KEYS
# =========================
try:
    from config_private import (
        API_TOKEN,
        GENERIC_API_KEY, GENERIC_API_KEY2, GENERIC_API_KEY3,
        GENERIC_API_KEY4, GENERIC_API_KEY5, GENERIC_API_KEY6,
        GOOGLE_API_KEY, GOOGLE_API_KEY2,
        OPENROUTER_API_KEY,
        giphy_api_key,
        KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY,
        GIGACHAT_API_KEY, GIGACHAT_CLIENT_ID,
        CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN,
        HUGGINGFACE_TOKEN,
    )
except ImportError:
    raise RuntimeError("config_private.py not found")

# =========================
# GEMINI KEYS POOL
# =========================
GEMINI_KEYS_POOL: List[str] = [
    k for k in [
        GENERIC_API_KEY,
        GENERIC_API_KEY2,
        GENERIC_API_KEY3,
        GENERIC_API_KEY4,
        GENERIC_API_KEY5,
        GENERIC_API_KEY6,
        GOOGLE_API_KEY,
        GOOGLE_API_KEY2,
    ] if k
]

if not GEMINI_KEYS_POOL:
    raise RuntimeError("CRITICAL: No Gemini API keys provided")

# =========================
# GLOBAL CONSTANTS
# =========================
BLOCKED_USERS = [354145389]
ADMIN_ID = 126386976
SPECIAL_CHAT_ID = -1001707530786

SEARCH_ENGINE_ID = "33026288e406447ea"

GIGACHAT_MODEL = "GigaChat-2"
GIGACHAT_MODEL_PRO = "GigaChat-2-Pro"
GIGACHAT_MODEL_MAX = "GigaChat-2-Max"

# =========================
# MODELS (ONLY REAL)
# =========================
MODEL_QUEUE_DEFAULT = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

MODEL_QUEUE_SPECIAL = [
    "gemini-2.5-pro",
] + MODEL_QUEUE_DEFAULT

IMAGE_MODEL_NAME = "imagen-3.0-generate-001"
EDIT_IMAGE_MODEL_NAME = "gemini-2.0-flash-preview-image-generation"
ROBOTICS_MODEL = "gemini-robotics-er-1.5-preview"

TTS_MODELS_QUEUE = [
    "gemini-2.5-flash-preview-tts"
]

# =========================
# RATE LIMIT CONTROL
# =========================
KEY_COOLDOWN_SECONDS = 180        # cooldown after 429
GLOBAL_MIN_DELAY = 0.35           # ~3 RPS global

_last_global_call = 0.0


def global_throttle():
    global _last_global_call
    now = time.time()
    delta = now - _last_global_call
    if delta < GLOBAL_MIN_DELAY:
        time.sleep(GLOBAL_MIN_DELAY - delta)
    _last_global_call = time.time()


# =========================
# GEMINI CLIENT POOL
# =========================
class GeminiClientPool:
    def __init__(self, keys: List[str]):
        self.keys = keys
        self.cooldowns: Dict[str, float] = {k: 0.0 for k in keys}

    def available_keys(self) -> List[str]:
        now = time.time()
        return [k for k, t in self.cooldowns.items() if now >= t]

    def mark_cooldown(self, key: str):
        self.cooldowns[key] = time.time() + KEY_COOLDOWN_SECONDS

    def get_model(self, key: str, model_name: str) -> genai.GenerativeModel:
        genai.configure(api_key=key)
        return genai.GenerativeModel(model_name)


CLIENT_POOL = GeminiClientPool(GEMINI_KEYS_POOL)

# =========================
# FALLBACK CHAT SESSION
# =========================
class FallbackChatSession:
    def __init__(
        self,
        wrapper: "ModelFallbackWrapper",
        initial_history: Optional[List[Dict[str, Any]]] = None,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ):
        self.wrapper = wrapper
        self.history: List[Dict[str, Any]] = initial_history or []
        self.chat_id = chat_id
        self.user_id = user_id

    def send_message(self, content: str):
        prompt = list(self.history)
        prompt.append({"role": "user", "parts": [content]})

        response = self.wrapper.generate_content(
            prompt,
            chat_id=self.chat_id,
            user_id=self.user_id,
        )

        self.history.append({"role": "model", "parts": [response.text]})
        return response


# =========================
# MODEL FALLBACK WRAPPER
# =========================
class ModelFallbackWrapper:
    def __init__(self, default_queue: List[str], special_queue: List[str]):
        self.default_queue = default_queue
        self.special_queue = special_queue
        self.last_used_model_name: Optional[str] = None

    def _get_queue(self, chat_id: Optional[int]):
        if chat_id == SPECIAL_CHAT_ID:
            return self.special_queue
        return self.default_queue

    def generate_content(self, prompt, chat_id=None, user_id=None):
        queue = self._get_queue(chat_id)

        last_error = None

        for model_name in queue:
            keys = CLIENT_POOL.available_keys()
            random.shuffle(keys)

            for api_key in keys:
                try:
                    global_throttle()
                    model = CLIENT_POOL.get_model(api_key, model_name)
                    result = model.generate_content(prompt)
                    self.last_used_model_name = model_name
                    return result

                except exceptions.ResourceExhausted as e:
                    logging.warning(
                        f"429 Gemini | model={model_name} | key=...{api_key[-4:]}"
                    )
                    CLIENT_POOL.mark_cooldown(api_key)
                    last_error = e

                except Exception as e:
                    logging.warning(
                        f"Gemini error | model={model_name} | {e}"
                    )
                    last_error = e

        raise last_error or RuntimeError("All Gemini models unavailable")

    def generate_custom(self, model_name: str, prompt, chat_id=None, user_id=None):
        last_error = None
        keys = CLIENT_POOL.available_keys()
        random.shuffle(keys)

        for api_key in keys:
            try:
                global_throttle()
                model = CLIENT_POOL.get_model(api_key, model_name)
                return model.generate_content(prompt)

            except exceptions.ResourceExhausted as e:
                CLIENT_POOL.mark_cooldown(api_key)
                last_error = e

            except Exception as e:
                last_error = e

        raise last_error or RuntimeError(f"Model {model_name} unavailable")

    def start_chat(self, history=None, chat_id=None, user_id=None):
        return FallbackChatSession(
            self,
            initial_history=history,
            chat_id=chat_id,
            user_id=user_id,
        )

    @property
    def model_names(self):
        return self.default_queue


# =========================
# INIT MODELS
# =========================
model = ModelFallbackWrapper(
    MODEL_QUEUE_DEFAULT,
    MODEL_QUEUE_SPECIAL
)

image_model = IMAGE_MODEL_NAME
edit_model = EDIT_IMAGE_MODEL_NAME
robotics_model = ROBOTICS_MODEL
tts_models = TTS_MODELS_QUEUE

# =========================
# FILES / STATE
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

DAILY_PROMPT = None
LAST_PROMPT_UPDATE = None

DIALOG_ENABLED = True
MAX_HISTORY_LENGTH = 20

# =========================
# AIROGRAM
# =========================
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()

# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    filename="bot_log.txt",
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)
