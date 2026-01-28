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

# Импорт всех wrapper-классов из нового модуля
from wrapper import (
    GroqWrapper,
    ModelFallbackWrapper,
    GigaChatWrapper,
    FallbackChatSession
)

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
# === ПРОЧИЕ КОНСТАНТЫ ===
# =========================
SEARCH_ENGINE_ID = "33026288e406447ea"

BLOCKED_USERS = [354145389]
ADMIN_ID = 126386976
SPECIAL_CHAT_ID = -1001707530786

# =========================
# === МОДЕЛИ AI (GEMINI, GIGACHAT, GROQ) ===
# =========================

# --- GEMINI MODEL QUEUES ---
MODEL_QUEUE_DEFAULT = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
]

MODEL_QUEUE_SPECIAL = [
    "gemini-2.5-pro",
] + MODEL_QUEUE_DEFAULT

# --- GEMINI PUBLIC MODEL CONSTANTS ---
TEXT_GENERATION_MODEL_LIGHT = "gemini-2.0-flash-lite-preview-02-05"
ROBOTICS_MODEL = "gemini-robotics-er-1.5-preview"

TTS_MODELS_QUEUE = [
    "gemini-2.5-flash-preview-tts"
]

# --- GIGACHAT MODEL QUEUES ---
GIGACHAT_MODEL_QUEUE_DEFAULT = ["GigaChat-2"]
GIGACHAT_MODEL_QUEUE_SPECIAL = ["GigaChat-2-Max"]

# --- GROQ MODELS ---
# для чотам (картинки: считывание), скаламбурь, добавь, нарисуй, опиши
GROQ_VISION_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct"

# для диалогов, пирожки, порошки, днд, чотам (текст, картинки: обработка считывания), 
# пародия, кто я, что за чат, кем стать, викторина
GROQ_TEXT_MODEL = "openai/gpt-oss-120b"

# для чотам (аудио)
GROQ_AUDIO_MODEL = "whisper-large-v3"

# для упупа скажи
GROQ_TTS_MODEL = "canopylabs/orpheus-v1-english"

# для чобыло
GROQ_SUMMARIZATION_MODEL = "groq/compound-mini"

# =========================
# === ИНИЦИАЛИЗАЦИЯ GROQ (ПОСЛЕ ОБЪЯВЛЕНИЯ КОНСТАНТ) ===
# =========================
groq_ai = GroqWrapper(
    GROQ_API_KEY,
    vision_model=GROQ_VISION_MODEL,
    text_model=GROQ_TEXT_MODEL,
    audio_model=GROQ_AUDIO_MODEL,
    tts_model=GROQ_TTS_MODEL,
    summarization_model=GROQ_SUMMARIZATION_MODEL
)

# =========================
# === STATIC MODELS ===
# =========================
search_model = genai.GenerativeModel("gemini-2.5-flash")
image_model = genai.GenerativeModel("imagen-3.0-generate-001")
edit_model = genai.GenerativeModel(
    "models/gemini-2.0-flash-preview-image-generation"
)

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
