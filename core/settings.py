"""Ключи, секреты и константы. Единственный модуль, знающий про config_private/env.

Никаких объектов с состоянием здесь нет — только значения.
"""
import os

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
        GENERIC_API_KEY8,
        GENERIC_API_KEY9,
        GENERIC_API_KEY10,
        OPENROUTER_API_KEY,
        SILICONFLOW_API_KEY,
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
    GENERIC_API_KEY2 = os.getenv("GENERIC_API_KEY2")
    GENERIC_API_KEY3 = os.getenv("GENERIC_API_KEY3")
    GENERIC_API_KEY4 = os.getenv("GENERIC_API_KEY4")
    GENERIC_API_KEY5 = os.getenv("GENERIC_API_KEY5")
    GENERIC_API_KEY6 = os.getenv("GENERIC_API_KEY6")
    GENERIC_API_KEY8 = os.getenv("GENERIC_API_KEY8")
    GENERIC_API_KEY9 = os.getenv("GENERIC_API_KEY9")
    GENERIC_API_KEY10 = os.getenv("GENERIC_API_KEY10")
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
    GOOGLE_API_KEY2 = os.getenv("GOOGLE_API_KEY2")
    giphy_api_key = os.getenv("giphy_api_key")
    KANDINSKY_API_KEY = os.getenv("KANDINSKY_API_KEY")
    KANDINSKY_SECRET_KEY = os.getenv("KANDINSKY_SECRET_KEY")
    GIGACHAT_API_KEY = os.getenv("GIGACHAT_API_KEY")
    GIGACHAT_CLIENT_ID = os.getenv("GIGACHAT_CLIENT_ID")
    CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
    HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_TOKEN")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY")

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
        GENERIC_API_KEY8,
        GENERIC_API_KEY9,
        GENERIC_API_KEY10,
    ]
    if key
]

if not GEMINI_KEYS_POOL:
    raise RuntimeError("⚠ Gemini API keys not found")

PRIMARY_GEMINI_KEY = GEMINI_KEYS_POOL[0]

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
# ВАЖНО: gemini-2.0-* отключены Google 1 июня 2026 — в очередь не добавлять.
# Порядок = приоритет: сначала лучший по качество/квота, в конце gemma-фоллбэки.
MODEL_QUEUE_DEFAULT = [
    "gemini-3.1-flash-lite",   # GA с мая 2026, быстрее и умнее 2.5-flash-lite
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3-flash-preview",  # preview: квоты жёстче, поэтому после GA-моделей
    "gemma-3-12b-it",
    "gemma-3-4b-it",
]

MODEL_QUEUE_SPECIAL = [
    #"gemini-2.5-pro",
] + MODEL_QUEUE_DEFAULT

# --- GEMINI PUBLIC MODEL CONSTANTS ---
TEXT_GENERATION_MODEL_LIGHT = "gemini-3.1-flash-lite"
ROBOTICS_MODEL = "gemini-robotics-er-1.5-preview"

TTS_MODELS_QUEUE = [
    "gemini-2.5-flash-preview-tts"
]

# --- GIGACHAT MODEL QUEUES ---
GIGACHAT_MODEL_QUEUE_DEFAULT = ["GigaChat-2"]
GIGACHAT_MODEL_QUEUE_SPECIAL = ["GigaChat-2-Max"]

# --- GROQ MODELS ---
# для чотам (картинки: считывание), скаламбурь, добавь, нарисуй, опиши
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

# для диалогов, пирожки, порошки, днд, чотам (текст, картинки: обработка считывания), 
# пародия, кто я, что за чат, кем стать, викторина
GROQ_TEXT_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct" #"llama-3.3-70b-versatile"

# для чотам (аудио)
GROQ_AUDIO_MODEL = "whisper-large-v3"

# для упупа скажи
GROQ_TTS_MODEL = "canopylabs/orpheus-v1-english"

# для чобыло
GROQ_SUMMARIZATION_MODEL = "groq/compound-mini"

