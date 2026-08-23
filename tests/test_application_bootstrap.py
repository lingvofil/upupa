import asyncio

# Настраивает fake env и моки тяжёлых библиотек до импорта app.bootstrap.
from tests import test_smoke_imports  # noqa: F401

from app.bootstrap import QUIZ_CHAT_IDS, UpupaApplication, create_application


class RecordingSupervisor:
    def __init__(self):
        self.names = []

    def start(self, coro, *, name):
        self.names.append(name)
        coro.close()
        return None

    async def stop(self):
        return None


def test_create_application_allows_dependency_injection():
    fake_bot = object()
    fake_dispatcher = object()
    supervisor = RecordingSupervisor()

    application = create_application(
        bot_instance=fake_bot,
        dispatcher=fake_dispatcher,
        supervisor=supervisor,
    )

    assert application.bot is fake_bot
    assert application.dispatcher is fake_dispatcher
    assert application.supervisor is supervisor


def test_background_task_set_is_explicit_and_idempotent():
    supervisor = RecordingSupervisor()
    application = UpupaApplication(
        bot=object(),
        dispatcher=object(),
        supervisor=supervisor,
    )

    application.start_background_tasks()
    application.start_background_tasks()

    assert supervisor.names == [
        *(f"daily-quiz:{chat_id}" for chat_id in QUIZ_CHAT_IDS),
        "birthday-scheduler",
        "holiday-scheduler",
        "proactive-loop",
        "crocodile-socket-server",
    ]


def test_main_delegates_to_application_runner(monkeypatch):
    import main

    calls = []

    async def fake_run_application():
        calls.append("run")

    monkeypatch.setattr(main, "run_application", fake_run_application)
    asyncio.run(main.main())

    assert calls == ["run"]
