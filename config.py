import google.generativeai as genai
from google.api_core import exceptions
import logging
from aiogram import Bot, Dispatcher, Router
import requests
import json
from typing import List, Dict, Any, Optional, Union
from gigachat import GigaChat
import os

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

SEARCH_ENGINE_ID = "33026288e406447ea"
GIGACHAT_MODEL = 'GigaChat-2'
GIGACHAT_MODEL_PRO = 'GigaChat-2-Pro'
GIGACHAT_MODEL_MAX = 'GigaChat-2-Max'

# Настройка клиента Gemini (Google Generative AI)
genai.configure(api_key=GENERIC_API_KEY)

# Список моделей в порядке приоритета
MODEL_QUEUE = [
    'gemini-2.5-pro',                        # 2 RPM (в минуту) / 50 RPD (в день)
    'gemini-2.5-flash-preview-09-2025',     # 10 RPM (в минуту) / 250 RPD (в день)
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
                logging.warning(f"⚠️ Лимит токенов исчерпан для {model_name}. Переключаюсь на следующую...")
                continue # Идем к следующей модели в списке
            
            except Exception as e:
                # Ловим другие ошибки (например, если gemini-2.5 еще не существует или 503 Service Unavailable)
                logging.error(f"❌ Ошибка модели {model_name}: {e}")
                last_error = e
                continue # Идем к следующей модели
        
        # Если цикл закончился, а ответа нет
        logging.error("🔥 Все модели недоступны!")
        raise last_error if last_error else Exception("Все модели исчерпаны.")

model = ModelFallbackWrapper(MODEL_QUEUE)
advanced_model = genai.GenerativeModel('gemini-2.0-flash') #gemini-2.5-pro-exp-03-25
image_model = genai.GenerativeModel("models/gemini-2.0-flash")
edit_model = genai.GenerativeModel("models/gemini-2.0-flash-preview-image-generation")

# Класс для работы с OpenRouter, имитирующий интерфейс genai.GenerativeModel
class OpenRouterModel:
    def __init__(self, model_name: str):
        """
        Инициализирует модель OpenRouter
        
        Args:
            model_name: Название модели на OpenRouter, например 'google/gemini-2.5-pro-exp-03-25:free'
        """
        self.model_name = model_name
        self.api_key = OPENROUTER_API_KEY
        self.url = "https://openrouter.ai/api/v1/chat/completions"
        
    def generate_content(self, 
                         prompt: Union[str, List[Dict[str, Any]]], 
                         temperature: float = 0.7, 
                         max_tokens: int = 1024,
                         site_url: str = None,
                         site_name: str = None) -> 'OpenRouterResponse':
        """
        Генерирует текст на основе промпта, имитируя интерфейс genai
        
        Args:
            prompt: Текстовый запрос для модели или список контента (текст, изображения)
            temperature: Температура генерации (креативность)
            max_tokens: Максимальное количество токенов в ответе
            site_url: URL сайта для рейтингов на openrouter.ai (опционально)
            site_name: Название сайта для рейтингов на openrouter.ai (опционально)
            
        Returns:
            Объект OpenRouterResponse с текстом ответа в атрибуте text
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Добавляем опциональные заголовки если они предоставлены
        if site_url:
            headers["HTTP-Referer"] = site_url
        if site_name:
            headers["X-Title"] = site_name
        
        # Подготовка контента в зависимости от типа промпта
        if isinstance(prompt, str):
            # Если простой строковый промпт
            content = prompt
            content_list = [{"type": "text", "text": content}]
        else:
            # Если уже структурированный список контента
            content_list = prompt
        
        data = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": content_list
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        response = requests.post(
            self.url,
            headers=headers,
            data=json.dumps(data)
        )
        
        if response.status_code != 200:
            error_info = response.json().get("error", {})
            error_message = error_info.get("message", "Неизвестная ошибка")
            raise Exception(f"Ошибка API: {error_message} (код {response.status_code})")
        
        result = response.json()
        
        # Создаем объект, имитирующий структуру ответа genai
        return OpenRouterResponse(result)
    
    def create_multimodal_content(self, text: str, image_urls: List[str]) -> List[Dict[str, Any]]:
        """
        Создает мультимодальный контент для запроса
        
        Args:
            text: Текстовый запрос
            image_urls: Список URL изображений
            
        Returns:
            Список объектов контента для API запроса
        """
        content = [{"type": "text", "text": text}]
        
        for url in image_urls:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": url
                }
            })
            
        return content


class OpenRouterResponse:
    """Класс для представления ответа от OpenRouter в формате, похожем на ответ genai"""
    
    def __init__(self, response_data: Dict[str, Any]):
        self.response_data = response_data
        self.text = self._extract_text()
        
    def _extract_text(self) -> str:
        """Извлекает текст из ответа OpenRouter"""
        choices = self.response_data.get("choices", [])
        if not choices:
            return ""
        
        message = choices[0].get("message", {})
        return message.get("content", "")
model2 = OpenRouterModel('openai/gpt-3.5-turbo')
advanced_model2 = OpenRouterModel('anthropic/claude-3-haiku')

# Чаты, юзеры
BLOCKED_USERS = [354145389]  # Список заблокированных пользователей
ADMIN_ID = 126386976  # ID пользователя, который может обновлять чаты
SPECIAL_CHAT_ID = -1001707530786  # Чат, который должен быть первым

# Основные настройки в файлах
CHAT_SETTINGS_FILE = "chat_settings.json"
LOG_FILE = "user_messages.log"  # Файл с историей сообщений
STATS_FILE = "message_stats.json"  # Файл для хранения статистики
CHAT_LIST_FILE = "chats.json"
SMS_DISABLED_CHATS_FILE = "sms_disabled_chats.json"  # Файл для хранения чатов с отключёнными смс
DB_FILE = "statistics.db" 

# Глобальные переменные и состояния
chat_settings = {}  # Глобальный словарь с настройками чатов
conversation_history = {} # Словарь для хранения истории разговоров
message_stats = {} # Словарь статистики {chat_id: {user_id: {"total": 0, "daily": 0, "weekly": 0}}}
quiz_questions = {}
quiz_states = {}  # Для хранения состояния викторин в разных чатах
chat_list = []  # Глобальный список чатов
sms_disabled_chats = set()  # Глобальный список чатов, где смс отключены
# ИСПРАВЛЕНИЕ: Переносим определение переменной сюда
ANTISPAM_ENABLED_CHATS = set() # Множество чатов, где включен антиспам-фильтр

# Переменные для контроля ежедневного промпта
DAILY_PROMPT = None
LAST_PROMPT_UPDATE = None
DIALOG_ENABLED = True  # Флаг для включения/отключения диалога
MAX_HISTORY_LENGTH = 20 # Максимальная длина истории диалога


# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    filename="bot_log.txt",
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
