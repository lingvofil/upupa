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
        GIGACHAT_API_KEY, GIGACHAT_CLIENT_ID
    )
except ImportError:
    print("Warning: config_private.py not found. Attempting to load from environment variables.")
    API_TOKEN = os.getenv('API_TOKEN', '')
    GENERIC_API_KEY = os.getenv('GENERIC_API_KEY', '')
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
    # ... остальные заглушки ...

SEARCH_ENGINE_ID = "33026288e406447ea"
GIGACHAT_MODEL = 'GigaChat-2'
GIGACHAT_MODEL_PRO = 'GigaChat-2-Pro'
GIGACHAT_MODEL_MAX = 'GigaChat-2-Max'

# === НАСТРОЙКА GEMINI ===
genai.configure(api_key=GENERIC_API_KEY)

# Список моделей. Бот будет пробовать их по очереди.
MODEL_QUEUE = [
    'gemini-2.5-flash-preview-09-2025',     # 10 RPM (в минуту) / 250 RPD (в день)
    'gemini-2.5-pro',                        # 2 RPM (в минуту) / 50 RPD (в день)
    'gemini-2.5-flash',                      # 10 RPM (в минуту) / 250 RPD (в день)
    'gemini-2.0-flash',                      # 15 RPM (в минуту) / 200 RPD (в день)
    'gemini-2.5-flash-lite-preview-09-2025',# 15 RPM (в минуту) / 1000 RPD (в день)
    'gemini-2.5-flash-lite',                 # 15 RPM (в минуту) / 1000 RPD (в день)
    'gemini-2.0-flash-lite',                 # 30 RPM (в минуту) / 200 RPD (в день)
    'gemini-1.5-flash'                       # 15 RPM (в минуту) / 50 RPD (в день)
]

class FallbackChatSession:
    """
    Класс-обертка для чат-сессии.
    Позволяет менять модель "на лету" прямо внутри диалога.
    """
    def __init__(self, wrapper, initial_history=None):
        self.wrapper = wrapper
        # Нам нужно инициализировать "настоящую" историю в формате Google.
        # Для этого мы создаем временную сессию с первой рабочей моделью.
        self._history = []
        self._init_real_history(initial_history)

    def _init_real_history(self, initial_history):
        """Создает реальный объект истории Google, чтобы dnd.py мог его редактировать."""
        for model_name in self.wrapper.model_names:
            try:
                model = genai.GenerativeModel(model_name)
                # Запускаем чат, чтобы получить объект истории правильного типа
                chat = model.start_chat(history=initial_history)
                self._history = chat.history
                logging.info(f"✅ Чат инициализирован через {model_name}")
                return
            except Exception as e:
                logging.warning(f"⚠️ Не удалось инициализировать чат через {model_name}: {e}")
                continue
        # Если ни одна модель не смогла создать историю (вряд ли), создаем пустой список
        if self._history is None:
            self._history = []

    @property
    def history(self):
        """Возвращает историю. dnd.py использует это свойство."""
        return self._history

    @history.setter
    def history(self, value):
        self._history = value

    def send_message(self, content):
        """
        Отправляет сообщение, перебирая модели при ошибках.
        """
        last_error = None
        
        for model_name in self.wrapper.model_names:
            try:
                # 1. Создаем свежую модель
                current_model = genai.GenerativeModel(model_name)
                
                # 2. Загружаем в неё ТЕКУЩУЮ историю
                # (Важно: передаем self._history, который мог быть изменен в dnd.py)
                chat = current_model.start_chat(history=self._history)
                
                # 3. Пытаемся отправить сообщение
                response = chat.send_message(content)
                
                # 4. Если успех - обновляем нашу сохраненную историю
                self._history = chat.history
                return response

            except exceptions.ResourceExhausted:
                logging.warning(f"⚠️ Лимит исчерпан для {model_name} в чате. Пробую следующую...")
                continue
            except Exception as e:
                # Ловим 404 (модель не найдена) и другие ошибки
                logging.error(f"❌ Ошибка в чате с моделью {model_name}: {e}")
                last_error = e
                continue
        
        raise last_error if last_error else Exception("Все модели в чате недоступны.")


class ModelFallbackWrapper:
    def __init__(self, model_names):
        self.model_names = model_names

    def generate_content(self, *args, **kwargs):
        """Обычная генерация (для команд типа 'пирожок' и т.д.)"""
        last_error = None
        for model_name in self.model_names:
            try:
                current_model = genai.GenerativeModel(model_name)
                return current_model.generate_content(*args, **kwargs)
            except (exceptions.ResourceExhausted, Exception) as e:
                logging.warning(f"⚠️ Ошибка генерации на {model_name}: {e}. Пробую следующую...")
                last_error = e
                continue
        raise last_error if last_error else Exception("Все модели исчерпаны.")

    def start_chat(self, history=None):
        """
        Возвращает нашу 'Умную сессию', которая умеет менять модели.
        """
        return FallbackChatSession(self, initial_history=history)

# Создаем "умную" модель
model = ModelFallbackWrapper(MODEL_QUEUE)

# Остальные модели
advanced_model = genai.GenerativeModel('gemini-2.0-flash') 
image_model = genai.GenerativeModel("models/gemini-2.0-flash")
edit_model = genai.GenerativeModel("models/gemini-2.0-flash-preview-image-generation")

# === НАСТРОЙКИ И ПЕРЕМЕННЫЕ ===
BLOCKED_USERS = [354145389]
ADMIN_ID = 126386976
SPECIAL_CHAT_ID = -1001707530786

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
