"""Ключи, секреты и константы. Единственный модуль, знающий про config_private/env.

Никаких объектов с состоянием здесь нет — только значения.
"""
import os

# Loopback only; use the same value for the service and the healthcheck CLI.
HEALTHCHECK_PORT = int(os.getenv("UPUPA_HEALTHCHECK_PORT", "8766"))

# Historical Upupa scheduling/user-facing time is Moscow time. Keep one
# explicit application timezone instead of depending on the VPS local timezone.
APP_TIMEZONE_NAME = os.getenv("UPUPA_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"

# Shared guard for synchronous AI SDK calls. Background work gets its own
# smaller lane while all provider calls share the same process-wide ceiling.
AI_MAX_CONCURRENCY = max(1, int(os.getenv("UPUPA_AI_MAX_CONCURRENCY", "3")))
AI_BACKGROUND_MAX_CONCURRENCY = max(
    1,
    min(
        AI_MAX_CONCURRENCY,
        int(os.getenv("UPUPA_AI_BACKGROUND_MAX_CONCURRENCY", "1")),
    ),
)
AI_QUEUE_TIMEOUT_SECONDS = max(
    1.0,
    float(os.getenv("UPUPA_AI_QUEUE_TIMEOUT_SECONDS", "20")),
)
AI_REQUEST_TIMEOUT_SECONDS = max(
    AI_QUEUE_TIMEOUT_SECONDS,
    float(os.getenv("UPUPA_AI_REQUEST_TIMEOUT_SECONDS", "120")),
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

# Optional feature keys may be added to config_private independently from the
# legacy required import block above. Environment variables take precedence.
try:
    import config_private as _config_private
except ImportError:
    _config_private = None

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY") or (
    getattr(_config_private, "OPENWEATHER_API_KEY", None)
    if _config_private is not None
    else None
)

# AI Horde supports anonymous access with the documented 0000000000 key. Keep
# it as the default reserve while allowing a registered key to raise priority.
AIHORDE_API_KEY = os.getenv("AIHORDE_API_KEY") or (
    getattr(_config_private, "AIHORDE_API_KEY", None)
    if _config_private is not None
    else None
) or "0000000000"

# New deployments may use Hugging Face's conventional HF_TOKEN name, while
# older Upupa code and config_private keep using HUGGINGFACE_TOKEN.
HUGGINGFACE_TOKEN = os.getenv("HF_TOKEN") or HUGGINGFACE_TOKEN

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

PRIMARY_GEMINI_KEY = GEMINI_KEYS_POOL[0] if GEMINI_KEYS_POOL else None


class SettingsValidationError(RuntimeError):
    """Required application settings are missing or invalid."""


def validate_required_settings() -> None:
    """Validate only resources required to start the Telegram application."""
    if not API_TOKEN or not str(API_TOKEN).strip():
        raise SettingsValidationError(
            "API_TOKEN is required to start the Telegram bot"
        )

# =========================
# === ПРОЧИЕ КОНСТАНТЫ ===
# =========================
SEARCH_ENGINE_ID = "33026288e406447ea"

BLOCKED_USERS = [354145389]
# Usernames are stored without "@" and compared case-insensitively.
# Keep this as a fallback for users whose numeric Telegram ID is not known yet.
BLOCKED_USERNAMES = {"mev515"}
BLOCKED_USERS_PATH = os.getenv("UPUPA_BLOCKED_USERS_PATH", "blocked_users.json")
ADMIN_ID = 126386976
SPECIAL_CHAT_ID = -1001707530786

# =========================
# === МОДЕЛИ AI (GEMINI, GIGACHAT, GROQ) ===
# =========================

# --- GEMINI MODEL QUEUES ---
# ВАЖНО: gemini-2.0-* отключены Google 1 июня 2026 — в очередь не добавлять.
# Очередь проверена живыми запросами с тремя production-ключами 22.09.2026.
# gemini-3.7-flash давал 503, gemini-3.5-* — 504, gemini-3.6-flash —
# пустой текст, gemma-3-*-it — 404 для generateContent.
MODEL_QUEUE_DEFAULT = [
    "gemini-2.5-flash",       # быстрый стабильный primary
    "gemini-2.5-flash-lite",  # самый быстрый из проверенных fallback
    "gemini-3-flash-preview", # рабочий preview-резерв
    "gemini-3.1-flash-lite",  # оставляем последним: периодические 503
]

# "упупа умоляю ..." — отдельный high-quality route. Если 3.8 недоступен,
# автоматически откатываемся на обычную проверенную очередь.
MODEL_QUEUE_PLEADING = [
    "gemini-3.8-flash",
] + MODEL_QUEUE_DEFAULT

MODEL_QUEUE_SPECIAL = [
    #"gemini-2.5-pro",
] + MODEL_QUEUE_DEFAULT

# --- GEMINI PUBLIC MODEL CONSTANTS ---
TEXT_GENERATION_MODEL_LIGHT = "gemini-2.5-flash-lite"
ROBOTICS_MODEL = "gemini-robotics-er-1.5-preview"

TTS_MODELS_QUEUE = [
    "gemini-2.5-flash-preview-tts"
]

# --- GIGACHAT MODEL QUEUES ---
# Wrapper идёт по списку слева направо и переключается только при ошибке.
# Обычные чаты: экономим основной большой Lite-пул, затем усиливаем качество.
GIGACHAT_MODEL_QUEUE_DEFAULT = [
    "GigaChat-2",
    "GigaChat-2-Pro",
    "GigaChat-2-Max",
    "GigaChat-3-Ultra",
]

# Специальный чат: качество прежде всего, Lite оставляем последним резервом.
GIGACHAT_MODEL_QUEUE_SPECIAL = [
    "GigaChat-3-Ultra",
    "GigaChat-2-Max",
    "GigaChat-2-Pro",
    "GigaChat-2",
]

# --- GROQ MODELS ---
# для чотам (картинки: считывание), скаламбурь, добавь, нарисуй, опиши
GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

# для диалогов, пирожки, порошки, днд, чотам (текст, картинки: обработка считывания), 
# пародия, кто я, что за чат, кем стать, викторина
GROQ_TEXT_MODEL = "openai/gpt-oss-120b"  # available for the current Groq key

# для чотам (аудио)
GROQ_AUDIO_MODEL = "whisper-large-v3"

# для упупа скажи
GROQ_TTS_MODEL = "canopylabs/orpheus-v1-english"

# для чобыло
GROQ_SUMMARIZATION_MODEL = "groq/compound-mini"
