# config.py
import google.generativeai as genai
from google.api_core import exceptions
import logging
from aiogram import Bot, Dispatcher, Router
import requests
import json
import random
import time
from typing import List, Dict, Any, Optional, Union
from gigachat import GigaChat
import os

# =========================
# === БЛОК ИМПОРТА КЛЮЧЕЙ ===
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
    print("Warning: config_private.py not found.")
    API_TOKEN = os.getenv("API_TOKEN")
    GENERIC_API_KEY = os.getenv("GENERIC_API_KEY")
    GENERIC_API_KEY2 = os.getenv("GENERIC_API_KEY2")
    GENERIC_API_KEY3 = os.getenv("GENERIC_API_KEY3")
    GENERIC_API_KEY4 = os.getenv("GENERIC_API_KEY4")
    GENERIC_API_KEY5 = os.getenv("GENERIC_API_KEY5")
    GENERIC_API_KEY6 = os.getenv("GENERIC_API_KEY6")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    GOOGLE_API_KEY2 = os.getenv("GOOGLE_API_KEY2")

# =========================
# === ПУЛ КЛЮЧЕЙ GEMINI ===
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
        GOOGLE_API_KEY2
    ] if key
]

if not GEMINI_KEYS_POOL:
    print("CRITICAL WARNING: No Gemini API keys found!")
    GEMINI_KEYS_POOL = ["dummy_key"]

# =========================
# === RATE LIMIT CONTROL ===
# =========================
KEY_COOLDOWN_SECONDS = 180
GLOBAL_MIN_DELAY = 0.35

_key_cooldowns: Dict[str, float] = {k: 0.0 for k in GEMINI_KEYS_POOL}
_last_global_call = 0.0

def _global_throttle():
    global _last_global_call
    now = time.time()
    delta = now - _last_global_call
    if delta < GLOBAL_MIN_DELAY:
        time.sleep(GLOBAL_MIN_DELAY - delta)
    _last_global_call = time.time()

def _available_keys() -> List[str]:
    now = time.time()
    return [k for k, t in _key_cooldowns.items() if now >= t]

def _mark_key_cooldown(key: str):
    _key_cooldowns[key] = time.time() + KEY_COOLDOWN_SECONDS

# =========================
# === ПРОЧИЕ КОНСТАНТЫ ===
# =========================
SEARCH_ENGINE_ID = "33026288e406447ea"
GIGACHAT_MODEL = "GigaChat-2"
GIGACHAT_MODEL_PRO = "GigaChat-2-Pro"
GIGACHAT_MODEL_MAX = "GigaChat-2-Max"

BLOCKED_USERS = [354145389]
ADMIN_ID = 126386976
SPECIAL_CHAT_ID = -1001707530786

# =========================
# === НАСТРОЙКА GEMINI ===
# =========================
# ВАЖНО: configure оставляем, но дальше контролируем
genai.configure(api_key=random.choice(GEMINI_KEYS_POOL))

# =========================
# === ОЧЕРЕДИ МОДЕЛЕЙ ===
# =========================
MODEL_QUEUE_DEFAULT = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

MODEL_QUEUE_SPECIAL = [
    "gemini-2.5-pro",
] + MODEL_QUEUE_DEFAULT

# =========================
# === FALLBACK CHAT SESSION ===
# =========================
class FallbackChatSession:
    def __init__(
        self,
        wrapper,
        initial_history=None,
        model_queue=None,
        chat_id=None,
        user_id=None
    ):
        self.wrapper = wrapper
        self.model_queue = model_queue if model_queue else wrapper.default_queue
        self.chat_id = chat_id
        self.user_id = user_id
        self._history = initial_history or []

    @property
    def history(self):
        return self._history

    @history.setter
    def history(self, value):
        self._history = value

    def send_message(self, content):
        last_error = None
        for model_name in self.model_queue:
            # --- ИСПРАВЛЕНИЕ: ПРОВЕРКА КЛЮЧЕЙ ---
            keys_to_try = _available_keys()
            # Если нет свободных ключей, пробуем все ключи снова для новой модели
            if not keys_to_try:
                keys_to_try = list(GEMINI_KEYS_POOL)
            
            random.shuffle(keys_to_try)
            
            for api_key in keys_to_try:
                try:
                    _global_throttle()
                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel(model_name)
                    chat = model.start_chat(history=self._history)
                    response = chat.send_message(content)
                    self._history = chat.history
                    self.wrapper.last_used_model_name = model_name
                    return response
                except exceptions.ResourceExhausted as e:
                    logging.warning(
                        f"⚠️ Quota exceeded {model_name} (key ...{api_key[-4:]})"
                    )
                    _mark_key_cooldown(api_key)
                    last_error = e
                except Exception as e:
                    logging.error(f"Error in chat with {model_name}: {e}")
                    last_error = e
        
        raise last_error or Exception("Все модели и ключи недоступны")

# =========================
# === MODEL FALLBACK WRAPPER ===
# =========================
class ModelFallbackWrapper:
    def __init__(self, default_queue, special_queue):
        self.default_queue = default_queue
        self.special_queue = special_queue
        self.last_used_model_name = "Еще не использовалась"

    def _get_queue(self, chat_id=None):
        if chat_id and str(chat_id) == str(SPECIAL_CHAT_ID):
            return self.special_queue
        return self.default_queue

    def generate_content(self, *args, **kwargs):
        chat_id = kwargs.pop("chat_id", None)
        user_id = kwargs.pop("user_id", None)
        current_queue = self._get_queue(chat_id)
        
        last_error = None
        
        for model_name in current_queue:
            # --- ИСПРАВЛЕНИЕ: ПРОВЕРКА КЛЮЧЕЙ ---
            keys_to_try = _available_keys()
            # Если все ключи в кулдауне (например, от прошлой модели),
            # даем им шанс сработать на новой модели
            if not keys_to_try:
                keys_to_try = list(GEMINI_KEYS_POOL)

            random.shuffle(keys_to_try)
            
            for api_key in keys_to_try:
                try:
                    _global_throttle()
                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel(model_name)
                    
                    result = model.generate_content(*args, **kwargs)
                    
                    self.last_used_model_name = model_name
                    return result

                except exceptions.ResourceExhausted as e:
                    logging.warning(
                        f"⚠️ Quota exceeded {model_name} (key ...{api_key[-4:]})"
                    )
                    _mark_key_cooldown(api_key)
                    last_error = e
                except Exception as e:
                    logging.error(f"Error with {model_name}: {e}")
                    last_error = e
        
        # Если дошли сюда, значит реально ничего не сработало
        if last_error:
            raise last_error
        else:
            raise Exception("Все модели исчерпаны (доступных ключей нет)")

    def generate_custom(self, model_name, *args, **kwargs):
        last_error = None
        
        # --- ИСПРАВЛЕНИЕ: ПРОВЕРКА КЛЮЧЕЙ ---
        keys_to_try = _available_keys()
        if not keys_to_try:
             keys_to_try = list(GEMINI_KEYS_POOL)

        random.shuffle(keys_to_try)
        
        for api_key in keys_to_try:
            try:
                _global_throttle()
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel(model_name)
                return model.generate_content(*args, **kwargs)
            except exceptions.ResourceExhausted as e:
                _mark_key_cooldown(api_key)
                last_error = e
            except Exception as e:
                last_error = e

        raise last_error or Exception(f"Модель {model_name} недоступна")

    def start_chat(self, history=None, chat_id=None, user_id=None):
        queue = self._get_queue(chat_id)
        return FallbackChatSession(
            self,
            initial_history=history,
            model_queue=queue,
            chat_id=chat_id,
            user_id=user_id
        )

    @property
    def model_names(self):
        return self.default_queue

# =========================
# === СОЗДАНИЕ МОДЕЛЕЙ ===
# =========================
model = ModelFallbackWrapper(
    MODEL_QUEUE_DEFAULT,
    MODEL_QUEUE_SPECIAL
)

genai.configure(api_key=random.choice(GEMINI_KEYS_POOL))
search_model = genai.GenerativeModel("gemini-2.5-flash")
image_model = genai.GenerativeModel("imagen-3.0-generate-001")
edit_model = genai.GenerativeModel("models/gemini-2.0-flash-preview-image-generation")
TEXT_GENERATION_MODEL_LIGHT = "gemini-2.0-flash-lite-preview-02-05"
ROBOTICS_MODEL = "gemini-robotics-er-1.5-preview"
TTS_MODELS_QUEUE = [
    "gemini-2.5-flash-preview-tts"
]

# =========================
# === ФАЙЛЫ И СОСТОЯНИЯ ===
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
# === AIROGRAM ===
# =========================
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()

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
