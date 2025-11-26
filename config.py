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
    # Если config_private.py не найден, читаем из переменных окружения.
    # Если переменная окружения не установлена, используем заполнитель,
    # чтобы избежать ошибок при локальной разработке без этих ключей.
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

# === НАСТРОЙКА GEMINI ===
SPECIAL_CHAT_ID = -1001707530786
genai.configure(api_key=GENERIC_API_KEY)

# 1. Очередь для ВСЕХ ЧАТОВ (без gemini-2.5-pro)
DEFAULT_MODEL_QUEUE = [
    'gemini-2.5-flash-preview-09-2025',  # 10 RPM (в минуту) / 250 RPD (в день)
    'gemini-2.5-flash',                 # 10 RPM (в минуту) / 250 RPD (в день)
    'gemini-2.0-flash',                 # 15 RPM (в минуту) / 200 RPD (в день)
    'gemini-2.5-flash-lite-preview-09-2025', # 15 RPM (в минуту) / 1000 RPD (в день)
    'gemini-2.5-flash-lite',            # 15 RPM (в минуту) / 1000 RPD (в день)
    'gemini-2.0-flash-lite',            # 30 RPM (в минуту) / 200 RPD (в день)
    'gemini-1.5-flash'                  # 15 RPM (в минуту) / 50 RPD (в день)
]

# 2. Очередь для СПЕЦИАЛЬНОГО ЧАТА (с gemini-2.5-pro)
SPECIAL_CHAT_MODEL_QUEUE = [
    'gemini-2.5-pro',                   # 2 RPM (в минуту) / 50 RPD (в день)
    *DEFAULT_MODEL_QUEUE                # Все остальные модели в качестве запасного варианта
]


class FallbackChatSession:
    """
    Класс-обертка для чат-сессии.
    Позволяет менять модель "на лету" прямо внутри диалога.
    """
    def __init__(self, wrapper, initial_history=None):
        self.wrapper = wrapper
        self._history = []
        self._init_real_history(initial_history)

    def _init_real_history(self, initial_history):
        self._history = []
        for model_name in self.wrapper.model_names:
            try:
                model = genai.GenerativeModel(model_name)
                # Если initial_history не None, передаем его
                chat = model.start_chat(history=initial_history or [])
                self._history = chat.history
                # Если инициализация прошла успешно, запоминаем модель
                self.wrapper.last_used_model_name = model_name 
                logging.debug(f"Чат-сессия успешно инициализирована с моделью: {model_name}")
                return
            except Exception as e:
                logging.warning(f"⚠️ Ошибка инициализации чата с {model_name}: {e}")
                continue
        logging.error("Не удалось инициализировать чат-сессию ни с одной моделью.")
        if initial_history is not None:
             self._history = initial_history

    @property
    def history(self):
        return self._history

    @history.setter
    def history(self, value):
        self._history = value

    def send_message(self, content):
        last_error = None
        for model_name in self.wrapper.model_names:
            try:
                current_model = genai.GenerativeModel(model_name)
                chat = current_model.start_chat(history=self._history)
                
                response = chat.send_message(content)
                
                # УСПЕХ: Обновляем историю и запоминаем текущую рабочую модель
                self._history = chat.history
                self.wrapper.last_used_model_name = model_name 
                logging.info(f"Сообщение успешно обработано моделью: {model_name}")
                return response

            except exceptions.ResourceExhausted:
                logging.warning(f"⚠️ Лимит исчерпан для {model_name}. Переход к следующей модели.")
                continue
            except Exception as e:
                logging.warning(f"⚠️ Ошибка на {model_name}: {e}. Переход к следующей модели.")
                last_error = e
                continue
        
        error_message = "Все модели в очереди недоступны."
        logging.error(error_message)
        raise last_error if last_error else Exception(error_message)


class ModelFallbackWrapper:
    """
    Класс-обертка для генерации контента и чат-сессий с балансировкой моделей.
    """
    def __init__(self, model_names):
        self.model_names = model_names
        # Переменная для хранения последней успешной модели
        self.last_used_model_name = "Еще не использовалась"

    def generate_content(self, *args, **kwargs):
        last_error = None
        for model_name in self.model_names:
            try:
                current_model = genai.GenerativeModel(model_name)
                result = current_model.generate_content(*args, **kwargs)
                
                # УСПЕХ: Запоминаем модель
                self.last_used_model_name = model_name
                logging.info(f"Контент успешно сгенерирован моделью: {model_name}")
                return result
                
            except exceptions.ResourceExhausted as e:
                logging.warning(f"⚠️ Лимит исчерпан для {model_name}. Переход к следующей модели.")
                last_error = e
                continue
            except Exception as e:
                logging.warning(f"⚠️ Ошибка на {model_name}: {e}. Переход к следующей модели.")
                last_error = e
                continue
        
        error_message = "Все модели исчерпаны/недоступны для generate_content."
        logging.error(error_message)
        raise last_error if last_error else Exception(error_message)

    def start_chat(self, history=None):
        return FallbackChatSession(self, initial_history=history)

# Создаем две инстанцированные "умные" модели
default_model_wrapper = ModelFallbackWrapper(DEFAULT_MODEL_QUEUE)
special_chat_model_wrapper = ModelFallbackWrapper(SPECIAL_CHAT_MODEL_QUEUE)

def get_model_wrapper(chat_id):
    """
    Функция для получения соответствующего ModelFallbackWrapper на основе ID чата.
    Используйте ее в хэндлерах вместо прямого обращения к переменной 'model'.
    """
    if chat_id == SPECIAL_CHAT_ID:
        logging.debug("Выбран wrapper для специального чата (с gemini-2.5-pro).")
        return special_chat_model_wrapper
    logging.debug("Выбран wrapper по умолчанию.")
    return default_model_wrapper

# Остальные модели
search_model = genai.GenerativeModel('gemini-2.5-flash') 
image_model = genai.GenerativeModel("imagen-3.0-generate-001")
edit_model = genai.GenerativeModel("models/gemini-2.0-flash-preview-image-generation")

# === НАСТРОЙКИ И ПЕРЕМЕННЫЕ ===
BLOCKED_USERS = [354145389]
ADMIN_ID = 126386976

CHAT_SETTINGS_FILE = "chat_settings.json"
LOG_FILE = "user_messages.log"
STATS_FILE = "message_stats.json"
CHAT_LIST_FILE = "chats.json"
SMS_DISABLED_CHATS_FILE = "sms_disabled_chats.json"
DB_FILE = "statistics.db" 

# === ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ СОСТОЯНИЯ ===
# (Очень важно, чтобы они были здесь, иначе викторина не будет работать)
chat_settings = {}
conversation_history = {}
message_stats = {}

# Словари для викторин
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
