"""Явная сборка и запуск Telegram-приложения Упупы.

На этом этапе сохраняются legacy-синглтоны bot/dp из core.loader: многие модули
ещё импортируют bot через config.py. Владелец жизненного цикла уже находится здесь,
а создание самих внешних клиентов будет вынесено из import-time на следующих этапах.
"""

from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession

from AI.birthday_calendar import birthday_scheduler
from AI.dnd import dnd_router
from AI.quiz import schedule_daily_quiz
from core.loader import bot as legacy_bot
from core.loader import dp as legacy_dispatcher
from core.logging_setup import logger
from features.content_filter import ContentFilterMiddleware, load_antispam_settings
from features.proactive import proactive_loop
from features.statistics import PrivateRateLimitMiddleware
import features.statistics as bot_statistics
from games import crocodile
from handlers import ROUTERS
from services.holidays import schedule_daily_holidays

from app.lifecycle import TaskSupervisor
from core.middlewares import IncomingMessageLogMiddleware


QUIZ_CHAT_IDS = (-1001707530786, -1001781970364)

_main_router: Router | None = None


def get_main_router() -> Router:
    """Собрать родительский router один раз, сохранив исторический порядок handlers."""
    global _main_router
    if _main_router is not None:
        return _main_router

    router = Router(name="main")
    router.message.middleware(IncomingMessageLogMiddleware())
    router.message.middleware(ContentFilterMiddleware())
    router.message.middleware(PrivateRateLimitMiddleware())

    for child_router in ROUTERS:
        router.include_router(child_router)

    _main_router = router
    return router


@dataclass
class UpupaApplication:
    bot: Bot
    dispatcher: Dispatcher
    supervisor: TaskSupervisor = field(default_factory=TaskSupervisor)
    _dispatcher_configured: bool = field(default=False, init=False)
    _background_tasks_started: bool = field(default=False, init=False)

    def initialize_state(self) -> None:
        load_antispam_settings()
        bot_statistics.init_db()

    def start_background_tasks(self) -> None:
        if self._background_tasks_started:
            return

        for chat_id in QUIZ_CHAT_IDS:
            self.supervisor.start(
                schedule_daily_quiz(self.bot, chat_id),
                name=f"daily-quiz:{chat_id}",
            )

        self.supervisor.start(
            birthday_scheduler(self.bot),
            name="birthday-scheduler",
        )
        self.supervisor.start(
            schedule_daily_holidays(self.bot),
            name="holiday-scheduler",
        )
        self.supervisor.start(
            proactive_loop(self.bot),
            name="proactive-loop",
        )
        self.supervisor.start(
            crocodile.start_socket_server(),
            name="crocodile-socket-server",
        )
        self._background_tasks_started = True

    def configure_dispatcher(self) -> None:
        if self._dispatcher_configured:
            return

        # dnd_router исторически подключён отдельно и раньше общего main router,
        # поэтому на него не распространяются middleware main router.
        main_router = get_main_router()
        attached = tuple(getattr(self.dispatcher, "sub_routers", ()))

        if dnd_router not in attached:
            self.dispatcher.include_router(dnd_router)
        if main_router not in attached:
            self.dispatcher.include_router(main_router)

        self._dispatcher_configured = True

    def configure_bot_session(self) -> None:
        self.bot.session = AiohttpSession(timeout=60)

    async def run(self) -> None:
        self.initialize_state()

        try:
            self.start_background_tasks()
            self.configure_dispatcher()
            self.configure_bot_session()

            await self.bot.delete_webhook(drop_pending_updates=True)
            logger.info("Starting polling bot_id=%s", id(self.bot))
            print("MAIN BOT ID:", id(self.bot))
            await self.dispatcher.start_polling(self.bot, skip_updates=True)
        finally:
            await self.supervisor.stop()


def create_application(
    *,
    bot_instance: Bot | None = None,
    dispatcher: Dispatcher | None = None,
    supervisor: TaskSupervisor | None = None,
) -> UpupaApplication:
    """Создать объект приложения; параметры позволяют подменять инфраструктуру в тестах."""
    return UpupaApplication(
        bot=legacy_bot if bot_instance is None else bot_instance,
        dispatcher=legacy_dispatcher if dispatcher is None else dispatcher,
        supervisor=TaskSupervisor() if supervisor is None else supervisor,
    )


async def run_application() -> None:
    application = create_application()
    await application.run()
