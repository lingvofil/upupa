"""Пакет хэндлеров. ПОРЯДОК В ROUTERS КРИТИЧЕН:
он повторяет порядок регистрации хэндлеров в старом монолитном main.py.
aiogram матчит сообщение по роутерам последовательно, catch-all (dialog) — последний.
"""
from handlers import (
    basic, sms, stats_lexicon, media_search, games, media_tools,
    ai_modes, ai_profiles, ai_vision, ai_generation, birthdays,
    ai_summary, ai_prompts, video, dialog,
)

ROUTERS = [
    basic.router,
    sms.router,
    stats_lexicon.router,
    media_search.router,
    games.router,
    media_tools.router,
    ai_modes.router,
    ai_profiles.router,
    ai_vision.router,
    ai_generation.router,
    birthdays.router,
    ai_summary.router,
    ai_prompts.router,
    video.router,   # видеогенерация — до catch-all
    dialog.router,
]
