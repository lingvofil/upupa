"""Smoke-тест: все модули проекта импортируются без ошибок.

Запуск:  python -m pytest tests/ -v
Не требует реальных ключей — подставляет фейковые env-переменные.
Тяжёлые внешние библиотеки (moviepy, playwright и т.п.) мокаются,
чтобы тест шёл быстро и не требовал ffmpeg/браузеров.
"""
import os
import sys
import importlib
import types
import pytest

# --- фейковые секреты, чтобы config.py не упал на импорте ---
FAKE_ENV = {
    "API_TOKEN": "123456789:AAFakeTokenForSmokeTestsOnly_abcdefg",
    **{f"GENERIC_API_KEY{i if i else ''}": "fake" for i in ["", 2, 3, 4, 5, 6, 8, 9, 10]},
    "GOOGLE_API_KEY": "fake", "GOOGLE_API_KEY2": "fake",
    "GROQ_API_KEY": "fake", "OPENROUTER_API_KEY": "fake",
    "SILICONFLOW_API_KEY": "fake", "POLLINATIONS_API_KEY": "fake",
}
os.environ.update(FAKE_ENV)

# --- мокаем тяжёлые/необязательные для импорта библиотеки ---
from unittest.mock import MagicMock

HEAVY_LIBS = ["moviepy", "moviepy.editor",
              "moviepy.video", "moviepy.video.fx", "moviepy.video.fx.all",
              "moviepy.audio", "moviepy.audio.fx", "moviepy.audio.fx.all",
              "playwright", "playwright.async_api",
              "seam_carving", "socketio", "gradio_client"]
for lib in HEAVY_LIBS:
    if lib not in sys.modules:
        sys.modules[lib] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT_MODULES = [
    "config", "upupa_utils", "prompts", "broadcast", "channels_settings",
    "chat_settings", "common_settings", "content_filter", "crocodile",
    "distortion", "egra", "history_engine", "interactive_settings",
    "lexicon_settings", "media_change", "memegenerator", "middlewares",
    "nameinfo", "search", "sherlock", "smart_search", "sms_settings",
    "stat_rank_settings", "statistics", "weather", "ytp",
]
AI_MODULES = [
    "AI.wrapper", "AI.adddescribe", "AI.birthday_calendar", "AI.dnd",
    "AI.leveltravel", "AI.picgeneration", "AI.profession", "AI.quiz",
    "AI.random_reactions", "AI.summarize", "AI.talking", "AI.tutu",
    "AI.voice", "AI.whatisthere", "AI.whoparody",
]

@pytest.mark.parametrize("module_name", ROOT_MODULES + AI_MODULES)
def test_module_imports(module_name):
    importlib.import_module(module_name)

def test_main_imports():
    importlib.import_module("main")
