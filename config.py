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

class ModelFallbackWrapper:
    def __init__(self, model_names):
        self.model_names = model_names

    def generate_content(self, *args, **kwargs):
        """
        Пытается сгенерировать ответ, перебирая модели из списка.
        """
        last_error = None
        
        for model_name in self.model_names:
            try:
                # Инициализируем конкретную модель
                current_model = genai.GenerativeModel(model_name)
                # Пытаемся получить ответ
                return current_model.generate_content(*args, **kwargs)
            
            except exceptions.ResourceExhausted:
                logging.warning(f"⚠️ Лимит токенов исчерпан для {model_name}. Переключаюсь...")
                continue
            
            except Exception as e:
                # Ловим ошибки 404 (модель не найдена) или 503 (сервер занят)
                logging.error(f"❌ Ошибка модели {model_name}: {e}")
                last_error = e
                continue
        
        # Если цикл закончился, а ответа нет
        logging.error("🔥 Все модели недоступны!")
        raise last_error if last_error else Exception("Все модели исчерпаны.")

    def start_chat(self, history=None):
        """
        Метод-заглушка на случай, если какой-то модуль вызовет start_chat.
        Всегда использует первую доступную модель.
        """
        # Используем модель с конца списка (обычно 1.5-flash), так как она стабильнее для чата
        safe_model = self.model_names[-1] 
        return genai.GenerativeModel(safe_model).start_chat(history=history)

# Создаем "умную" модель
model = ModelFallbackWrapper(MODEL_QUEUE)

# Остальные модели
advanced_model = genai.GenerativeModel('gemini-2.0-flash') 
image_model = genai.GenerativeModel("models/gemini-2.0-flash")
edit_model = genai.GenerativeModel("models/gemini-2.0-flash-preview-image-generation")


# === OPENROUTER ===
class OpenRouterModel:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self.api_key = OPENROUTER_API_KEY
        self.url = "https://openrouter.ai/api/v1/chat/completions"
        
    def generate_content(self, prompt: Union[str, List[Dict[str, Any]]], 
                         temperature: float = 0.7, max_tokens: int = 1024,
                         site_url: str = None, site_name: str = None) -> 'OpenRouterResponse':
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        if site_url: headers["HTTP-Referer"] = site_url
        if site_name: headers["X-Title"] = site_name
        
        content_list = [{"type": "text", "text": prompt}] if isinstance(prompt, str) else prompt
        
        data = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": content_list}],
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        response = requests.post(self.url, headers=headers, data=json.dumps(data))
        if response.status_code != 200:
            raise Exception(f"Ошибка API OpenRouter: {response.text}")
        
        return OpenRouterResponse(response.json())
    
    def create_multimodal_content(self, text: str, image_urls: List[str]):
        content = [{"type": "text", "text": text}]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        return content

class OpenRouterResponse:
    def __init__(self, response_data: Dict[str, Any]):
        self.response_data = response_data
        self.text = self._extract_text()
        
    def _extract_text(self) -> str:
        choices = self.response_data.get("choices", [])
        return choices[0].get("message", {}).get("content", "") if choices else ""

model2 = OpenRouterModel('openai/gpt-3.5-turbo')
advanced_model2 = OpenRouterModel('anthropic/claude-3-haiku')


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
