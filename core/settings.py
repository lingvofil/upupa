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
    import config_private as _config_private
except ImportError:
    _config_private = None


def _private_or_env(name: str):
    # Resolve independently: one absent provider must not erase the bot token.
    return getattr(_config_private, name, os.getenv(name))


API_TOKEN = _private_or_env("API_TOKEN")
GENERIC_API_KEY = _private_or_env("GENERIC_API_KEY")
GENERIC_API_KEY2 = _private_or_env("GENERIC_API_KEY2")
GENERIC_API_KEY3 = _private_or_env("GENERIC_API_KEY3")
GENERIC_API_KEY4 = _private_or_env("GENERIC_API_KEY4")
GENERIC_API_KEY5 = _private_or_env("GENERIC_API_KEY5")
GENERIC_API_KEY6 = _private_or_env("GENERIC_API_KEY6")
GENERIC_API_KEY8 = _private_or_env("GENERIC_API_KEY8")
GENERIC_API_KEY9 = _private_or_env("GENERIC_API_KEY9")
GENERIC_API_KEY10 = _private_or_env("GENERIC_API_KEY10")
OPENROUTER_API_KEY = _private_or_env("OPENROUTER_API_KEY")
SILICONFLOW_API_KEY = _private_or_env("SILICONFLOW_API_KEY")
GOOGLE_API_KEY = _private_or_env("GOOGLE_API_KEY")
GOOGLE_API_KEY2 = _private_or_env("GOOGLE_API_KEY2")
giphy_api_key = _private_or_env("giphy_api_key")
KANDINSKY_API_KEY = _private_or_env("KANDINSKY_API_KEY")
KANDINSKY_SECRET_KEY = _private_or_env("KANDINSKY_SECRET_KEY")
GIGACHAT_API_KEY = _private_or_env("GIGACHAT_API_KEY")
GIGACHAT_CLIENT_ID = _private_or_env("GIGACHAT_CLIENT_ID")
CLOUDFLARE_ACCOUNT_ID = _private_or_env("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = _private_or_env("CLOUDFLARE_API_TOKEN")
HUGGINGFACE_TOKEN = _private_or_env("HUGGINGFACE_TOKEN")
GROQ_API_KEY = _private_or_env("GROQ_API_KEY")
POLLINATIONS_API_KEY = _private_or_env("POLLINATIONS_API_KEY")

# New optional feature keys retain their environment-first precedence.
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY") or (
    getattr(_config_private, "OPENWEATHER_API_KEY", None)
    if _config_private is not None
    else None
)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY") or (
    getattr(_config_private, "ELEVENLABS_API_KEY", None)
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
# Перепроверено 11.10.2026 scripts/probe_gemini_models.py на production-ключах
# (2 ключа x 2 раунда, плюс промпт размера ДнД ~15 тыс. символов):
# все flash/flash-lite ниже отвечают 4/4 и ставят теги; 3.5/3.6/3.7 больше не
# дают 503/504/пустой текст. gemini-3.5-flash исключён: выброс 15 с на длинном
# промпте (таймаут ДнД 12 с). *-pro на бесплатном тарифе — 429/404.
# У каждой модели своя суточная квота на проект, поэтому длинная очередь =
# больше запросов в сутки; исчерпанные пары ключ+модель пропускаются
# (infrastructure/ai/gemini_quota.py).
MODEL_QUEUE_DEFAULT = [
    "gemini-2.5-flash",       # быстрый стабильный primary для чата (~1.3 с)
    "gemini-3.5-flash-lite",  # ~1.1 с, без thinking
    "gemini-2.5-flash-lite",  # ~0.8 с
    "gemini-3.8-flash",       # ~2.3 с, лучшее качество из быстрых
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-3.7-flash",       # бывают выбросы до ~11 с
    "gemini-3.1-flash-lite",
]

# ДнД ценит качество сцены больше скорости чата; все модели здесь укладываются
# в 12-секундный HTTP-таймаут ДнД на промпте реального размера (3–6 с).
DND_MODEL_QUEUE = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
]

# "упупа умоляю ..." — отдельный high-quality route.
MODEL_QUEUE_PLEADING = ["gemini-3.8-flash"] + [
    name for name in MODEL_QUEUE_DEFAULT if name != "gemini-3.8-flash"
]

MODEL_QUEUE_SPECIAL = [
    #"gemini-2.5-pro",  # 404 для новых пользователей
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
