"""Пакет хэндлеров. ПОРЯДОК В ROUTERS КРИТИЧЕН:
он повторяет порядок регистрации хэндлеров в старом монолитном main.py.
aiogram матчит сообщение по роутерам последовательно, catch-all (dialog) — последний.
"""
from handlers import (
    crocodile_guesses, basic, sms, world_visit_media, world_visit_lifecycle,
    world_interactions, world_expansion, world_symbols, world_hub, stats_lexicon,
    media_search, games, media_tools, ai_modes, ai_profiles, ai_vision, ai_generation,
    birthdays, court, ai_summary, ai_prompts, video, channel, chronicle_reactions, chronicle,
    social_graph, social_navigation, radio, song, dialog,
)

ROUTERS = [
    # Правильный ответ активного Кракадила важнее любой одноимённой команды.
    # Фильтры этого роутера матчят только фактическое угадывание, поэтому
    # обычные команды вне игры продолжают обрабатываться как раньше.
    crocodile_guesses.router,
    basic.router,
    sms.router,
    # Мир Упупы: у каждой команды и каждого callback-префикса ровно один роутер.
    world_visit_media.router,  # медиа-показы во время екскурсии — до текстового lifecycle
    world_visit_lifecycle.router,  # приглашения, 24-часовой визит, екскурсия, отзывы
    world_interactions.router,  # дипломатические действия worldx:*, ответы на ноты, блоки, дары, звёзды
    world_expansion.router,  # санкции и Международный суд (текст, повестки, защита)
    world_symbols.router,  # государственные флаги/гербы
    world_hub.router,  # экраны хаба worldhub:* и команды чтения
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
    chronicle_reactions.router, # реакции сначала идут в соцграф и Летопись одним update
    chronicle.router, # команды «летопись» / «летопись @user»
    social_graph.router, # соцграф; reaction update уже перехвачен общим Chronicle transport
    social_navigation.router, # досье, роли, фракции и все кнопки соцграфа (sg:*)
    radio.router,   # Радио Упупы — до catch-all
    song.router,    # короткие песни YuE2 — до catch-all
    dialog.router,
]
