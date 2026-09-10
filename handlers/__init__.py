"""Пакет хэндлеров. ПОРЯДОК В ROUTERS КРИТИЧЕН:
он повторяет порядок регистрации хэндлеров в старом монолитном main.py.
aiogram матчит сообщение по роутерам последовательно, catch-all (dialog) — последний.
"""
from handlers import (
    crocodile_guesses, basic, sms, world, world_visit_media, world_visit_lifecycle, world_visit_decisions,
    world_interactions, world_expansion, world_symbols, world_hub, world_listing, stats_lexicon,
    media_search, games, media_tools, ai_modes, ai_profiles, ai_vision, ai_generation,
    birthdays, court, ai_summary, ai_prompts, video, channel, social_graph, radio, dialog,
)
from features.world.hub_ui import build_world_main_markup

# Несколько исторических World-роутеров умеют рисовать главное меню и матчятся
# в разном порядке. Подменяем их локальные builders одной канонической функцией,
# чтобы более ранний world_interactions не мог снова спрятать новые разделы.
world_interactions._main_markup = build_world_main_markup
world_expansion._main_markup = build_world_main_markup
world_hub._main_markup = build_world_main_markup

ROUTERS = [
    # Правильный ответ активного Кракадила важнее любой одноимённой команды.
    # Фильтры этого роутера матчят только фактическое угадывание, поэтому
    # обычные команды вне игры продолжают обрабатываться как раньше.
    crocodile_guesses.router,
    basic.router,
    sms.router,
    world_visit_media.router,  # медиа-показы во время екскурсии должны перехватываться до текстового lifecycle
    world_visit_lifecycle.router,  # 24-часовой жизненный цикл визита, екскурсия, отзывы и ручное завершение
    world_visit_decisions.router,  # legacy guard входящих приглашений; lifecycle перехватывает новые решения первым
    world_interactions.router,  # дипломатические действия перехватывают main/diplomacy callbacks хаба
    world_expansion.router,  # характеристики, санкции и международный суд — до старого hub
    world_symbols.router,  # государственные флаги/гербы и их callbacks
    world_hub.router,  # интерактивный Мир Упупы
    world_listing.router,
    world.router,
    stats_lexicon.router,
    media_search.router,
    games.router,
    media_tools.router,
    ai_modes.router,
    ai_profiles.router,
    ai_vision.router,
    ai_generation.router,
    birthdays.router,
    court.router,  # персистентный Суд Упупы — до старого «рассуди»/catch-all
    ai_summary.router,
    ai_prompts.router,
    video.router,   # видеогенерация — до catch-all
    channel.router, # админская команда канала — до catch-all
    social_graph.router, # соцграф + message_reaction — до catch-all
    radio.router,   # Радио Упупы — до catch-all
    dialog.router,
]
