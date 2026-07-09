# === main.py — точка входа ===
#
# Здесь только сборка и запуск. Хэндлеры живут в handlers/ (см. handlers/__init__.py),
# логика — в features/ services/ games/ AI/, инфраструктура — в core/.

import asyncio

from aiogram import Router
from aiogram.client.session.aiohttp import AiohttpSession

from core.loader import bot, dp
from core.logging_setup import logger  # noqa: F401 (инициализирует логирование)
from core.middlewares import IncomingMessageLogMiddleware
from features.content_filter import ContentFilterMiddleware, load_antispam_settings
from features.statistics import PrivateRateLimitMiddleware
import features.statistics as bot_statistics

from AI.dnd import dnd_router
from AI.quiz import schedule_daily_quiz
from AI.birthday_calendar import birthday_scheduler
from features.proactive import proactive_loop
from games import crocodile

from handlers import ROUTERS

# --- Родительский роутер с middleware ---
# ВАЖНО: dnd_router подключается ОТДЕЛЬНО и ДО main_router — на него middleware
# (контент-фильтр, лог, рейт-лимит) не действуют. Так было в монолитном main.py.
main_router = Router(name="main")
main_router.message.middleware(IncomingMessageLogMiddleware())
main_router.message.middleware(ContentFilterMiddleware())
main_router.message.middleware(PrivateRateLimitMiddleware())

for r in ROUTERS:
    main_router.include_router(r)


async def main():
    # --- антиспам ---
    load_antispam_settings()

    # --- статистика ---
    bot_statistics.init_db()

    # --- планировщики викторин ---
    chat_ids = ['-1001707530786', '-1001781970364']
    for chat_id in chat_ids:
        asyncio.create_task(
            schedule_daily_quiz(bot, int(chat_id))
        )

    # --- планировщик дней рождения ---
    asyncio.create_task(
        birthday_scheduler(bot)
    )

    # --- проактивный режим (вбросы в молчащие чаты) ---
    asyncio.create_task(
        proactive_loop(bot)
    )

    # --- КРОКОДИЛ: socket.io сервер ---
    # ВАЖНО: только create_task, без await
    asyncio.create_task(
        crocodile.start_socket_server()
    )

    # --- роутеры ---
    dp.include_router(dnd_router)
    dp.include_router(main_router)

    # --- HTTP-сессия бота ---
    bot.session = AiohttpSession(timeout=60)

    # --- polling ---
    # webhook гарантированно выключаем
    await bot.delete_webhook(drop_pending_updates=True)

    # стартуем polling (БЛОКИРУЮЩИЙ)
    print("MAIN BOT ID:", id(bot))
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
