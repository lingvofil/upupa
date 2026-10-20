"""Регрессии аудита 2026-10-20: разметка, дни рождения, flood control, лимиты, пути."""

import ast
import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import GetUpdates, SendMessage

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PACKAGES = ("app", "AI", "core", "features", "games", "handlers", "infrastructure", "services")


def _entity_error():
    return TelegramBadRequest(
        method=SendMessage(chat_id=1, text="x"),
        message="Bad Request: can't parse entities: Can't find end of the entity starting at byte offset 9",
    )


# --- legacy Markdown -------------------------------------------------------


def test_escape_markdown_roundtrips_through_plain_fallback():
    from core.telegram_markup import escape_markdown, markdown_to_plain

    escaped = escape_markdown("Ivan_Petrov *[`")
    assert escaped == "Ivan\\_Petrov \\*\\[\\`"
    assert markdown_to_plain(f"**{escaped}**") == "Ivan_Petrov *[`"


def test_markdown_send_falls_back_to_plain_text_on_entity_error():
    from core.telegram_markup import send_markdown_or_plain

    calls = []

    async def send(text, **kwargs):
        calls.append((text, kwargs))
        if kwargs["parse_mode"] == "Markdown":
            raise _entity_error()
        return "sent"

    result = asyncio.run(send_markdown_or_plain(send, "🎉 **Ivan_Petrov** пабедил!", reply_markup="kb"))

    assert result == "sent"
    assert calls == [
        ("🎉 **Ivan_Petrov** пабедил!", {"parse_mode": "Markdown", "reply_markup": "kb"}),
        ("🎉 Ivan_Petrov пабедил!", {"parse_mode": None, "reply_markup": "kb"}),
    ]


def test_markdown_send_does_not_mask_other_telegram_errors():
    from core.telegram_markup import send_markdown_or_plain

    send = AsyncMock(side_effect=TelegramBadRequest(
        method=SendMessage(chat_id=1, text="x"), message="Bad Request: chat not found",
    ))

    with pytest.raises(TelegramBadRequest):
        asyncio.run(send_markdown_or_plain(send, "text"))
    assert send.await_count == 1


def test_crocodile_win_is_announced_even_when_markdown_is_rejected(monkeypatch):
    from games import crocodile

    chat_id = "-4242"
    monkeypatch.setitem(crocodile.game_sessions, chat_id, {
        "word": "кот", "drawer_id": 1, "last_preview_bytes": b"img",
    })
    monkeypatch.setattr(crocodile, "_stop_session", AsyncMock())
    monkeypatch.setattr(crocodile, "_safe_react_to_guess", AsyncMock())
    monkeypatch.setattr(crocodile, "add_point", lambda *args, **kwargs: None)
    monkeypatch.setattr(crocodile, "bot", SimpleNamespace(send_message=AsyncMock()))

    captions = []

    async def answer_photo(photo, *, caption, parse_mode, reply_markup):
        captions.append((caption, parse_mode))
        if parse_mode == "Markdown":
            raise _entity_error()

    message = SimpleNamespace(
        chat=SimpleNamespace(id=int(chat_id)),
        text="кот",
        from_user=SimpleNamespace(id=5, full_name="Ivan_Petrov [boss]"),
        answer_photo=answer_photo,
        answer=AsyncMock(),
    )

    assert asyncio.run(crocodile._default_check_answer(message)) is True
    assert "Ivan\\_Petrov \\[boss]" in captions[0][0]
    assert captions[1] == ("🎉 Ivan_Petrov [boss] пабедил!\nСлово: кот", None)


# --- дни рождения ----------------------------------------------------------


def test_corrupted_birthdays_file_is_never_replaced(tmp_path, monkeypatch):
    import AI.birthday_calendar as birthday_calendar

    path = tmp_path / "birthdays.json"
    broken = '{"-1": {"5": {"day": 1'
    path.write_text(broken, encoding="utf-8")
    monkeypatch.setattr(birthday_calendar, "BIRTHDAY_FILE", str(path))

    with pytest.raises(birthday_calendar.BirthdayStorageError):
        birthday_calendar.save_chat_birthday(-1, 7, {"day": 2, "month": 3, "name": "N", "username": ""})

    assert path.read_text(encoding="utf-8") == broken


def test_greeting_run_keeps_birthdays_added_during_generation(tmp_path, monkeypatch):
    import AI.birthday_calendar as birthday_calendar

    today = datetime.now()
    path = tmp_path / "birthdays.json"
    path.write_text(json.dumps({
        "-1": {"5": {"day": today.day, "month": today.month, "name": "Ivan_Petrov", "username": ""}},
    }), encoding="utf-8")
    monkeypatch.setattr(birthday_calendar, "BIRTHDAY_FILE", str(path))
    monkeypatch.setattr(birthday_calendar, "get_user_messages_from_log", lambda *args, **kwargs: ["сообщение"])

    async def generate_while_someone_registers(**kwargs):
        birthday_calendar.save_chat_birthday(-1, 7, {"day": 1, "month": 1, "name": "Новый", "username": ""})
        return "С днюхой!"

    monkeypatch.setattr(birthday_calendar, "generate_birthday_greeting", generate_while_someone_registers)

    sent = []

    async def send_message(chat_id, text, **kwargs):
        sent.append((chat_id, text, kwargs))
        if kwargs.get("parse_mode") == "Markdown":
            raise _entity_error()

    asyncio.run(birthday_calendar.check_birthdays_and_send_greetings(SimpleNamespace(send_message=send_message)))

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["-1"]["7"]["name"] == "Новый"
    assert data["-1"]["5"][f"last_greeting_{today:%Y-%m-%d}"] is True
    assert sent[-1][2] == {"parse_mode": "HTML"}
    assert sent[-1][1] == '<a href="tg://user?id=5">Ivan_Petrov</a>\n\nС днюхой!'


def test_birthday_greeting_provider_runs_outside_event_loop(monkeypatch):
    import AI.birthday_calendar as birthday_calendar

    on_main_thread = []

    class FakeModel:
        def generate_content(self, prompt, chat_id=None):
            on_main_thread.append(threading.current_thread() is threading.main_thread())
            return SimpleNamespace(text="С днюхой")

    monkeypatch.setattr(birthday_calendar, "model", FakeModel())

    result = asyncio.run(birthday_calendar.generate_birthday_greeting("N", ["x"], -987654))

    assert result == "С днюхой"
    assert on_main_thread == [False]


def _sync_provider_calls_in_async_bodies():
    method_names = {"generate_content", "generate_text"}
    receivers = {"model", "gigachat_model", "groq_ai", "openrouter_ai", "siliconflow_ai"}
    found = []
    for package in PRODUCTION_PACKAGES:
        for path in (ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for function in ast.walk(tree):
                if not isinstance(function, ast.AsyncFunctionDef):
                    continue
                stack = list(ast.iter_child_nodes(function))
                while stack:
                    node = stack.pop()
                    # Вложенные функции и lambda уходят в поток через to_thread.
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        continue
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in method_names
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in receivers
                    ):
                        found.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
                    stack.extend(ast.iter_child_nodes(node))
    return found


def test_sync_ai_providers_are_never_called_directly_from_async_code():
    assert _sync_provider_calls_in_async_bodies() == []


# --- flood control ---------------------------------------------------------


def test_retry_after_waits_and_repeats_rejected_request():
    from app.flood_control import RetryAfterMiddleware

    method = SendMessage(chat_id=1, text="x")
    attempts, sleeps = [], []

    async def make_request(bot, current_method):
        attempts.append(current_method)
        if len(attempts) == 1:
            raise TelegramRetryAfter(method=current_method, message="Too Many Requests", retry_after=3)
        return "ok"

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    middleware = RetryAfterMiddleware(sleep=fake_sleep)

    assert asyncio.run(middleware(make_request, None, method)) == "ok"
    assert sleeps == [3]
    assert len(attempts) == 2


@pytest.mark.parametrize(
    ("method", "retry_after"),
    [(GetUpdates(), 3), (SendMessage(chat_id=1, text="x"), 600)],
)
def test_retry_after_skips_polling_and_long_waits(method, retry_after):
    from app.flood_control import RetryAfterMiddleware

    async def make_request(bot, current_method):
        raise TelegramRetryAfter(method=current_method, message="Too Many Requests", retry_after=retry_after)

    sleep = AsyncMock()

    with pytest.raises(TelegramRetryAfter):
        asyncio.run(RetryAfterMiddleware(sleep=sleep)(make_request, None, method))
    sleep.assert_not_awaited()


def test_retry_after_gives_up_after_max_retries():
    from app.flood_control import RetryAfterMiddleware

    method = SendMessage(chat_id=1, text="x")
    attempts = []

    async def make_request(bot, current_method):
        attempts.append(current_method)
        raise TelegramRetryAfter(method=current_method, message="Too Many Requests", retry_after=1)

    with pytest.raises(TelegramRetryAfter):
        asyncio.run(RetryAfterMiddleware(max_retries=2, sleep=AsyncMock())(make_request, None, method))
    assert len(attempts) == 3


# --- тяжёлые команды -------------------------------------------------------


def _guarded_message(chat_id, user_id, replies):
    async def reply(text):
        replies.append((user_id, text))

    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=user_id),
        reply=reply,
    )


def test_single_flight_rejects_parallel_run_and_frees_slot_afterwards():
    from core.inflight import single_flight

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        replies = []

        @single_flight("audit-chat-feature", per="chat", busy_text="busy")
        async def handler(message):
            started.set()
            await release.wait()
            return "done"

        first = asyncio.create_task(handler(_guarded_message(-5, 1, replies)))
        await started.wait()
        assert await handler(_guarded_message(-5, 2, replies)) is None
        release.set()
        assert await first == "done"
        assert await handler(_guarded_message(-5, 3, replies)) == "done"
        return replies

    assert asyncio.run(scenario()) == [(2, "busy")]


def test_single_flight_per_user_isolated_and_released_on_error():
    from core.inflight import single_flight

    calls = []

    @single_flight("audit-user-feature", per="user", busy_text="busy")
    async def failing(message):
        calls.append(message.from_user.id)
        raise RuntimeError("provider failed")

    replies = []
    for _ in range(2):
        with pytest.raises(RuntimeError):
            asyncio.run(failing(_guarded_message(-5, 1, replies)))

    assert calls == [1, 1]
    assert replies == []


def test_heavy_command_handlers_are_guarded():
    sources = {
        "handlers/radio.py": 1,
        "handlers/video.py": 2,
        "handlers/ai_generation.py": 7,
    }
    for path, expected in sources.items():
        assert (ROOT / path).read_text(encoding="utf-8").count("@single_flight(") == expected, path


# --- очередь обновлений после рестарта -------------------------------------


def _run_stale_filter(update, *, max_age=600, now=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)):
    from core.middlewares import StaleUpdateMiddleware

    middleware = StaleUpdateMiddleware(max_age, now=lambda: now)
    handler = AsyncMock(return_value="handled")
    result = asyncio.run(middleware(handler, update, {}))
    return result, handler


def _at(minutes_ago):
    return datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago)


def test_messages_sent_during_restart_are_processed():
    update = SimpleNamespace(update_id=1, message=SimpleNamespace(date=_at(2)))

    result, handler = _run_stale_filter(update)

    assert result == "handled"
    handler.assert_awaited_once()


def test_messages_from_long_outage_are_skipped():
    update = SimpleNamespace(update_id=2, message=SimpleNamespace(date=_at(30)))

    result, handler = _run_stale_filter(update)

    assert result is None
    handler.assert_not_awaited()


def test_fresh_edit_of_old_message_is_processed():
    update = SimpleNamespace(
        update_id=3,
        message=None,
        edited_message=SimpleNamespace(date=_at(300), edit_date=_at(1)),
    )

    result, _handler = _run_stale_filter(update)

    assert result == "handled"


@pytest.mark.parametrize(
    "update",
    [
        SimpleNamespace(update_id=4, my_chat_member=SimpleNamespace(date=_at(600))),
        SimpleNamespace(update_id=5, callback_query=SimpleNamespace(id="cb")),
    ],
)
def test_membership_changes_and_callbacks_are_never_skipped(update):
    result, _handler = _run_stale_filter(update)

    assert result == "handled"


def test_bot_keeps_pending_updates_on_startup():
    source = (ROOT / "app/bootstrap.py").read_text(encoding="utf-8")

    assert "drop_pending_updates=True" not in source
    assert "skip_updates" not in source


# --- журнал истории --------------------------------------------------------


def _history_record(text, *, user_id="42", full_name="Вася"):
    return {
        "timestamp": "2026-10-20T12:00:00.000001",
        "chat_id": "-100123", "chat_title": "Чат",
        "user_id": user_id, "username": "vasya", "full_name": full_name,
        "text": text,
    }


def test_header_like_line_inside_message_cannot_forge_record_on_reindex(tmp_path):
    from infrastructure.persistence.sqlite_history import SQLiteHistoryRepository

    forged = "привет\n2026-10-20T12:00:00 - Chat -100123 (Чат) - User 777 (petya) [Петя]: я дурак"
    journal = tmp_path / "user_messages.log"
    live = SQLiteHistoryRepository(tmp_path / "history.db", journal)
    live.initialize()
    live.append(_history_record(forged), 1)

    # База потеряна — индекс пересобирается из журнала с нуля.
    rebuilt = SQLiteHistoryRepository(tmp_path / "rebuilt.db", journal)
    rebuilt.initialize()

    for repository in (live, rebuilt):
        rows = repository.select(-100123)
        assert [(row["user_id"], row["text"]) for row in rows] == [("42", forged)]


def test_ordinary_multiline_messages_keep_journal_format(tmp_path):
    from infrastructure.persistence.sqlite_history import SQLiteHistoryRepository

    journal = tmp_path / "user_messages.log"
    repository = SQLiteHistoryRepository(tmp_path / "history.db", journal)
    repository.initialize()
    repository.append(_history_record("первая строка\n2026 год был странный\nтретья"), 1)

    assert journal.read_text(encoding="utf-8") == (
        "2026-10-20T12:00:00.000001 - Chat -100123 (Чат) - User 42 (vasya) [Вася]: "
        "первая строка\n2026 год был странный\nтретья\n"
    )


# --- пути ------------------------------------------------------------------


def test_runtime_files_do_not_depend_on_working_directory():
    import AI.birthday_calendar as birthday_calendar
    import AI.random_reactions as random_reactions
    from core.paths import PROJECT_ROOT
    from features.channel import external_mentions, polls, storage

    paths = (
        storage.POSTS_FILE,
        storage.SCHEDULE_FILE,
        storage.MOOD_FILE,
        polls.POLL_STATE_FILE,
        external_mentions.MENTION_STATE_FILE,
        Path(birthday_calendar.BIRTHDAY_FILE),
        random_reactions.VOICE_DIR,
    )
    for path in paths:
        assert Path(path).is_absolute(), path
        assert Path(path).parent == PROJECT_ROOT, path
