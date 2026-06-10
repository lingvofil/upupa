"""Контракт фасада config.py: все имена, которые проект импортирует
из config, обязаны существовать. Если тест падает — фасад потерял имя.
"""
import importlib
import pytest

# Импорт test_smoke_imports настраивает фейковые env и моки
from tests import test_smoke_imports  # noqa: F401

PUBLIC_NAMES = [
    # настройки и константы
    "API_TOKEN", "ADMIN_ID", "BLOCKED_USERS", "SPECIAL_CHAT_ID",
    "KANDINSKY_API_KEY", "KANDINSKY_SECRET_KEY", "POLLINATIONS_API_KEY",
    "GROQ_VISION_MODEL", "ROBOTICS_MODEL", "TEXT_GENERATION_MODEL_LIGHT",
    "TTS_MODELS_QUEUE",
    # пути к файлам
    "DB_FILE", "LOG_FILE", "STATS_FILE", "SMS_DISABLED_CHATS_FILE",
    # рантайм-состояние
    "chat_settings", "conversation_history", "message_stats",
    "quiz_questions", "quiz_states", "chat_list", "sms_disabled_chats",
    "ANTISPAM_ENABLED_CHATS",
    # объекты
    "bot", "dp", "router", "model", "groq_ai", "gigachat_model", "logger",
]


@pytest.mark.parametrize("name", PUBLIC_NAMES)
def test_facade_exports(name):
    config = importlib.import_module("config")
    assert hasattr(config, name), f"config-фасад потерял имя: {name}"


def test_state_objects_are_shared():
    """from config import X и from core.state import X — один и тот же объект."""
    import config
    from core import state
    assert config.chat_settings is state.chat_settings
    assert config.conversation_history is state.conversation_history
