"""Пакет prompts. Фасад: все старые импорты `from prompts import X` работают.

Содержимое:
  help_texts.py — HELP_DICT, HELP_TEXT
  ai_prompts.py — промпты для AI-функций
  personas.py   — персоны бота (PROMPTS_DICT) и функции доступа
  chat_data.py  — ранги, стоп-слова, каналы, queries, actions
"""
from prompts.help_texts import HELP_DICT, HELP_TEXT
from prompts.ai_prompts import (
    PARODY_PROMPT, MEME_SYSTEM_PROMPT, PROMPTS_MEDIA, PROMPT_DESCRIBE,
    SPECIAL_PROMPT, PROMPT_SERIOUS_MODE, CUSTOM_PROMPT_TEMPLATE,
    USER_IMITATION_BASE_PROMPT,
    PROMPT_PIROZHOK, PROMPT_PIROZHOK1, PROMPT_POROSHOK, PROMPT_POROSHOK1,
    KEYWORDS, DIALOG_TRIGGER_KEYWORDS,
)
from prompts.personas import (
    PROMPTS_DICT, PROMPTS_TEXT,
    get_prompt_by_name, get_available_prompts, get_prompts_list_text,
)
from prompts.chat_data import RANKS, STOPWORDS, CHANNEL_SETTINGS, queries, actions
