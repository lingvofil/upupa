"""Явная сборка и запуск Telegram-приложения Упупы.

Bot и Dispatcher создаются только в composition root. Прикладные модули
загружаются на startup, чтобы импорт bootstrap не запускал их side effects.
"""

from dataclasses import dataclass, field
from functools import partial

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession

from app.lifecycle import TaskSupervisor
from app.readiness import PollingHealth, ReadinessServer
from core.loader import configure_aiogram_components
from core.logging_setup import logger
from core.settings import API_TOKEN, HEALTHCHECK_PORT, validate_required_settings
from infrastructure.ai.execution import ai_execution_lane


QUIZ_CHAT_IDS = (-1001707530786, -1001781970364)
REQUIRED_BACKGROUND_TASKS = (
    *(f"daily-quiz:{chat_id}" for chat_id in QUIZ_CHAT_IDS),
    "birthday-scheduler", "holiday-scheduler", "proactive-loop", "channel-scheduler",
    "world-visit-expiration", "crocodile-session-persistence",
)

_main_router: Router | None = None


async def _run_background_ai(coro):
    """Propagate the background AI lane through scheduler call chains/to_thread."""
    with ai_execution_lane("background"):
        return await coro


def get_main_router() -> Router:
    """Собрать родительский router один раз, сохранив исторический порядок handlers."""
    global _main_router
    if _main_router is not None:
        return _main_router

    from core.middlewares import IncomingMessageLogMiddleware
    from features.content_filter import ContentFilterMiddleware
    from features.social_graph import SocialInteractionMiddleware
    from features.statistics import PrivateRateLimitMiddleware
    from handlers import ROUTERS

    router = Router(name="main")
    router.message.middleware(IncomingMessageLogMiddleware())
    router.message.middleware(ContentFilterMiddleware())
    router.message.middleware(PrivateRateLimitMiddleware())
    router.message.middleware(SocialInteractionMiddleware())

    for child_router in ROUTERS:
        router.include_router(child_router)

    _main_router = router
    return router


@dataclass
class UpupaApplication:
    bot: Bot
    dispatcher: Dispatcher
    supervisor: TaskSupervisor = field(default_factory=TaskSupervisor)
    polling_health: PollingHealth = field(default_factory=PollingHealth)
    _dispatcher_configured: bool = field(default=False, init=False)
    _background_tasks_started: bool = field(default=False, init=False)

    def initialize_state(self) -> None:
        from core.paths import HISTORY_DB_PATH, STATISTICS_DB_PATH, USER_MESSAGES_LOG_PATH, WORLD_DB_PATH
        from core.history_store import configure_history_repository
        from infrastructure.persistence.sqlite_history import SQLiteHistoryRepository
        from features.chat_settings import load_chat_state
        from features.content_filter import load_antispam_settings
        from features.sms_settings import load_sms_disabled_chats
        from features.social_graph import (
            configure_social_graph_repository,
            init_db as init_social_graph_db,
        )
        from features.stat_rank_settings import configure_counter_repository, load_stat_rank_state
        from infrastructure.persistence.sqlite_rank_counters import SQLiteRankCountersRepository
        import features.statistics as bot_statistics
        from features.world.service import WorldService, configure_world_service
        from infrastructure.persistence import (
            SQLiteSocialGraphRepository,
            SQLiteStatisticsRepository,
            SQLiteWorldRepository,
        )

        load_chat_state()
        load_antispam_settings()
        load_sms_disabled_chats()
        configure_counter_repository(SQLiteRankCountersRepository(STATISTICS_DB_PATH))
        load_stat_rank_state()
        bot_statistics.configure_statistics_repository(
            SQLiteStatisticsRepository(STATISTICS_DB_PATH)
        )
        bot_statistics.init_db()
        configure_social_graph_repository(SQLiteSocialGraphRepository(STATISTICS_DB_PATH))
        init_social_graph_db()

        world_repository = SQLiteWorldRepository(WORLD_DB_PATH)
        world_repository.init_schema()
        configure_world_service(WorldService(world_repository))
        history = SQLiteHistoryRepository(HISTORY_DB_PATH, USER_MESSAGES_LOG_PATH)
        history.initialize()
        configure_history_repository(history)

    def start_background_tasks(self) -> None:
        if self._background_tasks_started:
            return

        from AI.birthday_calendar import birthday_scheduler
        from AI.dnd import (
            configure_task_supervisor as configure_dnd_tasks,
            restore_dnd_sessions,
        )
        from AI.quiz import schedule_daily_quiz
        from features.channel.scheduler import channel_scheduler_loop
        from features.proactive import proactive_loop
        from features.world.interactions import visit_expiration_loop
        from games import crocodile
        from games.crocodile_canvas_restore import configure_crocodile_canvas_restore
        from games.crocodile_controls import configure_crocodile_controls
        from games.crocodile_persistence import (
            crocodile_session_persistence_loop,
            restore_crocodile_sessions,
        )
        from games.crocodile_single_words import configure_crocodile_single_words
        from services.holidays import schedule_daily_holidays

        configure_dnd_tasks(self.supervisor)
        crocodile.configure_task_supervisor(self.supervisor)
        configure_crocodile_controls()
        configure_crocodile_single_words()
        configure_crocodile_canvas_restore()
        restore_dnd_sessions(self.bot)
        restore_crocodile_sessions()
        crocodile._scores_load()

        for chat_id in QUIZ_CHAT_IDS:
            self.supervisor.start(
                _run_background_ai(schedule_daily_quiz(self.bot, chat_id)),
                name=f"daily-quiz:{chat_id}",
            )

        self.supervisor.start(
            _run_background_ai(birthday_scheduler(self.bot)),
            name="birthday-scheduler",
        )
        self.supervisor.start(
            _run_background_ai(schedule_daily_holidays(self.bot)),
            name="holiday-scheduler",
        )
        self.supervisor.start(
            _run_background_ai(proactive_loop(self.bot)),
            name="proactive-loop",
        )
        self.supervisor.start(
            _run_background_ai(channel_scheduler_loop(self.bot)),
            name="channel-scheduler",
        )
        self.supervisor.start(
            _run_background_ai(visit_expiration_loop(self.bot)),
            name="world-visit-expiration",
        )
        self.supervisor.start(
            crocodile_session_persistence_loop(),
            name="crocodile-session-persistence",
        )
        self.supervisor.start(
            crocodile.start_socket_server(),
            name="crocodile-socket-server",
        )
        self._background_tasks_started = True

    def configure_dispatcher(self) -> None:
        if self._dispatcher_configured:
            return

        from AI.dnd import dnd_router

        # dnd_router исторически подключён отдельно и раньше общего main router,
        # поэтому на него не распространяются middleware main router.
        main_router = get_main_router()
        attached = tuple(getattr(self.dispatcher, "sub_routers", ()))

        if dnd_router not in attached:
            self.dispatcher.include_router(dnd_router)
        if main_router not in attached:
            self.dispatcher.include_router(main_router)

        self._dispatcher_configured = True

    def create_readiness_server(self) -> ReadinessServer:
        from core.paths import HISTORY_DB_PATH, STATISTICS_DB_PATH, WORLD_DB_PATH
        from infrastructure.persistence.health import check_databases

        return ReadinessServer(
            self.polling_health, self.supervisor,
            partial(check_databases, STATISTICS_DB_PATH, WORLD_DB_PATH, HISTORY_DB_PATH),
            REQUIRED_BACKGROUND_TASKS, port=HEALTHCHECK_PORT,
        )

    async def run(self) -> None:
        readiness = self.create_readiness_server()
        self.bot.session.middleware(self.polling_health.observe)
        try:
            self.initialize_state()
            self.start_background_tasks()
            self.configure_dispatcher()

            await self.bot.delete_webhook(drop_pending_updates=True)
            await readiness.start()
            logger.info("Starting polling bot_id=%s", id(self.bot))
            await self.dispatcher.start_polling(
                self.bot, skip_updates=True, close_bot_session=False,
            )
        finally:
            try:
                await readiness.stop()
            finally:
                try:
                    await self.supervisor.stop()
                finally:
                    await self.bot.session.close()


def create_application(
    *,
    bot_instance: Bot | None = None,
    dispatcher: Dispatcher | None = None,
    supervisor: TaskSupervisor | None = None,
) -> UpupaApplication:
    """Создать и связать aiogram-ресурсы; тесты могут подменить инфраструктуру."""
    resolved_bot = bot_instance
    if resolved_bot is None:
        validate_required_settings()
        resolved_bot = Bot(
            token=API_TOKEN,
            session=AiohttpSession(timeout=60),
        )

    resolved_dispatcher = dispatcher if dispatcher is not None else Dispatcher()
    configure_aiogram_components(
        bot_instance=resolved_bot,
        dispatcher=resolved_dispatcher,
    )

    return UpupaApplication(
        bot=resolved_bot,
        dispatcher=resolved_dispatcher,
        supervisor=TaskSupervisor() if supervisor is None else supervisor,
    )


async def run_application() -> None:
    application = create_application()
    await application.run()
