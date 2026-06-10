"""Инициализация AI-клиентов: Gemini, Groq, GigaChat, OpenAI-совместимые."""
import google.generativeai as genai
from gigachat import GigaChat

from AI.wrapper import (
    GroqWrapper,
    ModelFallbackWrapper,
    GigaChatWrapper,
    FallbackChatSession,
    OpenAICompatibleWrapper,
)
from core.settings import (
    GEMINI_KEYS_POOL, PRIMARY_GEMINI_KEY,
    GROQ_API_KEY, GIGACHAT_API_KEY,
    OPENROUTER_API_KEY, SILICONFLOW_API_KEY,
    MODEL_QUEUE_DEFAULT, MODEL_QUEUE_SPECIAL,
    GIGACHAT_MODEL_QUEUE_DEFAULT, GIGACHAT_MODEL_QUEUE_SPECIAL,
    GROQ_VISION_MODEL, GROQ_TEXT_MODEL, GROQ_AUDIO_MODEL,
    GROQ_TTS_MODEL, GROQ_SUMMARIZATION_MODEL,
)

genai.configure(api_key=PRIMARY_GEMINI_KEY)

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
    MODEL_QUEUE_SPECIAL,
    keys_pool=GEMINI_KEYS_POOL
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

# === OPENAI-COMPATIBLE PROVIDERS ===
openrouter_ai = OpenAICompatibleWrapper(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
    model_name="openrouter/elephant-alpha",
)

siliconflow_ai = OpenAICompatibleWrapper(
    api_key=SILICONFLOW_API_KEY,
    base_url="https://api.siliconflow.com/v1",
    model_name="deepseek-ai/DeepSeek-V3.2",
)

