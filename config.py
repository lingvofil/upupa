import google.generativeai as genai
from google.api_core import exceptions
import logging
from aiogram import Bot, Dispatcher, Router
import requests
import json
from typing import List, Dict, Any, Optional, Union
from gigachat import GigaChat
import os

# === БЛОК ИМПОРТА КЛЮЧЕЙ ===
try:
    from config_private import (
        API_TOKEN,
        GENERIC_API_KEY3, GENERIC_API_KEY2, GENERIC_API_KEY4,
        GENERIC_API_KEY5, GENERIC_API_KEY6, GENERIC_API_KEY,
        OPENROUTER_API_KEY,
        GOOGLE_API_KEY, GOOGLE_API_KEY2,
        giphy_api_key,
        KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY,
        GIGACHAT_API_KEY, GIGACHAT_CLIENT_ID,
        CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN
    )
except ImportError:
    print("Warning: config_private.py not found. Attempting to load from environment variables or using placeholders.")
    API_TOKEN = os.getenv('API_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN_HERE')
    GENERIC_API_KEY3 = os.getenv('GENERIC_API_KEY3', 'YOUR_GEMINI_KEY3_HERE')
    GENERIC_API_KEY2 = os.getenv('GENERIC_API_KEY2', 'YOUR_GEMINI_KEY2_HERE')
    GENERIC_API_KEY4 = os.getenv('GENERIC_API_KEY4', 'YOUR_GEMINI_KEY4_HERE')
    GENERIC_API_KEY5 = os.getenv('GENERIC_API_KEY5', 'YOUR_GEMINI_KEY5_HERE')
    GENERIC_API_KEY6 = os.getenv('GENERIC_API_KEY6', 'YOUR_GEMINI_KEY6_HERE')
    GENERIC_API_KEY = os.getenv('GENERIC_API_KEY', 'YOUR_GEMINI_KEY_RUSIC_HERE')
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', 'YOUR_OPENROUTER_KEY_HERE')
    GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', 'YOUR_Google Search_KEY_HERE')
    GOOGLE_API_KEY2 = os.getenv('GOOGLE_API_KEY2', 'YOUR_Google Search_KEY2_HERE')
    giphy_api_key = os.getenv('GIPHY_API_KEY', 'YOUR_GIPHY_KEY_HERE')
    KANDINSKY_API_KEY = os.getenv('KANDINSKY_API_KEY', 'YOUR_KANDINSKY_KEY_HERE')
    KANDINSKY_SECRET_KEY = os.getenv('KANDINSKY_SECRET_KEY', 'YOUR_KANDINSKY_SECRET_HERE')
    GIGACHAT_API_KEY = os.getenv('GIGACHAT_API_KEY', 'YOUR_GIGACHAT_KEY_HERE')
    GIGACHAT_CLIENT_ID = os.getenv('GIGACHAT_CLIENT_ID', 'YOUR_GIGACHAT_CLIENT_ID_HERE')
    CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "NO_CF_ID")
    CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "NO_CF_TOKEN")

SEARCH_ENGINE_ID = "33026288e406447ea"
GIGACHAT_MODEL = 'GigaChat-2'
GIGACHAT_MODEL_PRO = 'GigaChat-2-Pro'
GIGACHAT_MODEL_MAX = 'GigaChat-2-Max'

# === ГЛОБАЛЬНЫЕ НАСТРОЙКИ ЧАТОВ ===
BLOCKED_USERS = [354145389]
ADMIN_ID = 126386976
SPECIAL_CHAT_ID = -1001707530786

# === НАСТРОЙКА GEMINI ===
genai.configure(api_key=GENERIC_API_KEY)

# 1. Очередь для ВСЕХ чатов (БЕЗ 2.5 Pro)
# Вернули как было (приоритет качества/новизны)
MODEL_QUEUE_DEFAULT = [
    'gemini-2.5-flash-preview-09-2025',      # 10 RPM
    'gemini-2.5-flash',                      # 10 RPM
    'gemini-2.0-flash',                      # 15 RPM
    'gemini-2.5-flash-lite-preview-09-2025',# 15 RPM
    'gemini-2.5-flash-lite',                 # 15 RPM
    'gemini-2.0-flash-lite',                 # 30 RPM
    'gemini-1.5-flash'                       # 15 RPM
]

# 2. Очередь ТОЛЬКО для специального чата (С 2.5 Pro)
MODEL_QUEUE_SPECIAL = [
    'gemini-2.5-pro',                        # 2 RPM - ставим первой
] + MODEL_QUEUE_DEFAULT

class FallbackChatSession:
    """
    Класс-обертка для чат-сессии.
    """
    def __init__(self, wrapper, initial_history=None, model_queue=None):
        self.wrapper = wrapper
        self.model_queue = model_queue if model_queue else wrapper.default_queue
        self._history = []
        self._init_real_history(initial_history)

    def _init_real_history(self, initial_history):
        for model_name in self.model_queue:
            try:
                model = genai.GenerativeModel(model_name)
                chat = model.start_chat(history=initial_history)
                self._history = chat.history
                self.wrapper.last_used_model_name = model_name 
                return
            except Exception:
                continue
        if self._history is None:
            self._history = []

    @property
    def history(self):
        return self._history

    @history.setter
    def history(self, value):
        self._history = value

    def send_message(self, content):
        last_error = None
        for model_name in self.model_queue:
            try:
                current_model = genai.GenerativeModel(model_name)
                chat = current_model.start_chat(history=self._history)
                
                response = chat.send_message(content)
                
                self._history = chat.history
                self.wrapper.last_used_model_name = model_name 
                return response

            except exceptions.ResourceExhausted:
                continue
            except Exception as e:
                last_error = e
                continue
        
        raise last_error if last_error else Exception("Все модели в чате недоступны.")


class ModelFallbackWrapper:
    """
    Умная обертка, которая выбирает очередь моделей в зависимости от chat_id.
    """
    def __init__(self, default_queue, special_queue):
        self.default_queue = default_queue
        self.special_queue = special_queue
        self.last_used_model_name = "Еще не использовалась"

    def _get_queue(self, chat_id=None):
        if chat_id and str(chat_id) == str(SPECIAL_CHAT_ID):
            return self.special_queue
        return self.default_queue

    def generate_content(self, *args, **kwargs):
        chat_id = kwargs.pop('chat_id', None)
        current_queue = self._get_queue(chat_id)
        last_error = None

        for model_name in current_queue:
            try:
                current_model = genai.GenerativeModel(model_name)
                result = current_model.generate_content(*args, **kwargs)
                
                self.last_used_model_name = model_name
                return result
                
            except (exceptions.ResourceExhausted, Exception) as e:
                logging.warning(f"⚠️ Ошибка на {model_name} (ChatID: {chat_id}): {e}")
                last_error = e
                continue
        raise last_error if last_error else Exception("Все модели исчерпаны.")

    def start_chat(self, history=None, chat_id=None):
        queue = self._get_queue(chat_id)
        return FallbackChatSession(self, initial_history=history, model_queue=queue)

    @property
    def model_names(self):
        return self.default_queue

# Создаем "умную" модель с двумя списками
model = ModelFallbackWrapper(MODEL_QUEUE_DEFAULT, MODEL_QUEUE_SPECIAL)

# Остальные модели
search_model = genai.GenerativeModel('gemini-2.5-flash') 
image_model = genai.GenerativeModel("imagen-3.0-generate-001")
edit_model = genai.GenerativeModel("models/gemini-2.0-flash-preview-image-generation")

# Спец. модель для генерации ТЕКСТА озвучки (чтобы не тратить квоту основной модели)
TEXT_GENERATION_MODEL_LIGHT = 'gemini-2.0-flash-lite-preview-02-05'

# Очередь для TTS (Аудио)
TTS_MODELS_QUEUE = [
    "gemini-2.5-flash-preview-tts"
]

# === НАСТРОЙКИ И ПЕРЕМЕННЫЕ ===
CHAT_SETTINGS_FILE = "chat_settings.json"
LOG_FILE = "user_messages.log"
STATS_FILE = "message_stats.json"
CHAT_LIST_FILE = "chats.json"
SMS_DISABLED_CHATS_FILE = "sms_disabled_chats.json"
DB_FILE = "statistics.db" 

# === ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ СОСТОЯНИЯ ===
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

# === ИНИЦИАЛИЗАЦИЯ ===
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()

logging.basicConfig(
    level=logging.INFO,
    filename="bot_log.txt",
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
