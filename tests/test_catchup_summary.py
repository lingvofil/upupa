import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.summary_commands import summary_mode
from infrastructure.persistence.sqlite_history import SQLiteHistoryRepository


@pytest.fixture
def history(tmp_path, monkeypatch):
    from tests import test_smoke_imports
    import core.history_store as store

    del test_smoke_imports
    repo = SQLiteHistoryRepository(tmp_path / "history.db", tmp_path / "user_messages.log")
    repo.initialize()
    monkeypatch.setattr(store, "_repository", repo)
    return repo


def append(repo, text, *, hours=0, chat=-1):
    repo.append(dict(timestamp=(datetime.now() - timedelta(hours=hours)).isoformat(),
                     chat_id=chat, user_id=2, chat_title="Чат", username="alice",
                     full_name="Алиса", text=text))


def message(user=2, chat=-1):
    status = SimpleNamespace(delete=AsyncMock(), edit_text=AsyncMock())
    return SimpleNamespace(chat=SimpleNamespace(id=chat), from_user=SimpleNamespace(id=user),
                           sender_chat=None, reply=AsyncMock(return_value=status), answer=AsyncMock(),
                           bot=SimpleNamespace(send_chat_action=AsyncMock()))


@pytest.mark.parametrize("text,expected", [
    ("ЧОБЫЛО", "recent"), (" упупа чобыло? ", "recent"),
    ("Что я пропустил?", "catchup"), ("Упупа что я пропустила!", "catchup"),
    ("интересно, что я пропустил сегодня", None), (None, None),
])
def test_command_modes(text, expected):
    assert summary_mode(text) == expected


def test_cursor_survives_restart_is_scoped_and_never_regresses(history):
    now = datetime.now()
    append(history, "первое")
    boundary, previous = history.summary_boundary(-1, 2)
    assert previous is None
    history.acknowledge_summary(-1, 2, boundary, now)
    history.acknowledge_summary(-1, 2, boundary - 1, now - timedelta(days=1))
    reopened = SQLiteHistoryRepository(history.path, history.log_path)
    reopened.initialize()
    assert reopened.summary_boundary(-1, 2)[1]["through_id"] == boundary
    assert reopened.summary_boundary(-1, 3)[1] is None
    assert reopened.summary_boundary(-2, 2)[1] is None


def test_shared_prompt_boundaries_and_personal_readers(history, monkeypatch):
    import AI.summarize as summary

    append(history, "ДРЕВНЕЕ", hours=13)
    append(history, "СВЕЖЕЕ", hours=1)
    append(history, "Что я пропустил?")
    provider = AsyncMock(return_value="Готовая сводка")
    monkeypatch.setattr(summary, "_generate_with_active_model", provider)
    monkeypatch.setattr(summary, "build_prompt_with_current_chat_prompt", lambda chat, prompt, **kw: prompt)

    async def run():
        async def during_generation(*args, **kwargs):
            append(history, "ПОЗЖЕ")
            return "Готовая сводка"

        provider.side_effect = during_generation
        first = message()
        assert await summary.summarize_chat_history(first, None, history.log_path, ["typing"], catchup=True)
        first_prompt = provider.call_args.args[0]
        assert "СВЕЖЕЕ" in first_prompt and "ДРЕВНЕЕ" not in first_prompt
        assert "Что я пропустил?" not in first_prompt and "ПОЗЖЕ" not in first_prompt
        provider.side_effect = None
        assert await summary.summarize_chat_history(message(), None, history.log_path, ["typing"], catchup=True)
        second_prompt = provider.call_args.args[0]
        assert "ПОЗЖЕ" in second_prompt and "СВЕЖЕЕ" not in second_prompt
        # The task instructions are identical; only the period and input change.
        assert first_prompt.split("Вот сообщения:")[0] == second_prompt.split("Вот сообщения:")[0]
        provider.reset_mock()
        append(history, "УПУПА ЧТО Я ПРОПУСТИЛА!")
        assert await summary.summarize_chat_history(message(), None, history.log_path, ["typing"], catchup=True)
        provider.assert_not_called()
        # A different reader still receives their own initial 12-hour window.
        await summary.summarize_chat_history(message(user=3), None, history.log_path, ["typing"], catchup=True)
        assert "СВЕЖЕЕ" in provider.call_args.args[0]
        # Fixed-period mode remains repeatable, and also marks the delivered window.
        await summary.summarize_chat_history(message(), None, history.log_path, ["typing"])
        assert "СВЕЖЕЕ" in provider.call_args.args[0]
        provider.reset_mock()
        await summary.summarize_chat_history(message(), None, history.log_path, ["typing"], catchup=True)
        provider.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["empty", "provider", "delivery", "partial_delivery"])
def test_failure_keeps_cursor_and_allows_retry(history, monkeypatch, failure):
    import AI.summarize as summary

    append(history, "Не потерять")
    provider = AsyncMock(return_value="" if failure == "empty" else "Результат")
    if failure == "provider":
        provider.side_effect = RuntimeError("provider unavailable")
    if failure == "partial_delivery":
        provider.return_value = "a" * 5000
    monkeypatch.setattr(summary, "_generate_with_active_model", provider)
    first = message()
    if failure == "delivery":
        async def fail_result(text, **kwargs):
            if text == "Результат":
                raise RuntimeError("Telegram unavailable")
            return SimpleNamespace(delete=AsyncMock(), edit_text=AsyncMock())

        first.reply.side_effect = fail_result
    if failure == "partial_delivery":
        first.answer.side_effect = RuntimeError("second part unavailable")

    async def run():
        assert not await summary.summarize_chat_history(first, None, history.log_path, ["typing"], catchup=True)
        assert history.summary_boundary(-1, 2)[1] is None
        provider.side_effect = None
        provider.return_value = "Повторная сводка"
        assert await summary.summarize_chat_history(message(), None, history.log_path, ["typing"], catchup=True)
        assert history.summary_boundary(-1, 2)[1] is not None

    asyncio.run(run())


def test_catchup_keeps_long_absence_and_excludes_other_chats(history):
    import AI.summarize as summary

    append(history, "Уже было", hours=48)
    boundary, _ = history.summary_boundary(-1, 2)
    history.acknowledge_summary(-1, 2, boundary, datetime.now() - timedelta(hours=24))
    append(history, "За время отсутствия", hours=20)
    append(history, "Чужой чат", chat=-2)
    window = summary._indexed_summary_window(history, "-1", 2, datetime.now(), True)
    assert [row["text"] for row in window[0]] == ["За время отсутствия"]
    recent = summary._indexed_summary_window(history, "-1", 2, datetime.now(), False)
    assert recent[0] == []


def test_anonymous_catchup_does_not_create_shared_cursor(history, monkeypatch):
    import AI.summarize as summary

    append(history, "Сообщение")
    provider = AsyncMock()
    monkeypatch.setattr(summary, "_generate_with_active_model", provider)
    anonymous = message()
    anonymous.sender_chat = SimpleNamespace(id=-1)
    assert not asyncio.run(summary.summarize_chat_history(anonymous, None, history.log_path, ["typing"], catchup=True))
    provider.assert_not_called()
    assert history.summary_boundary(-1, 2)[1] is None


def test_concurrent_request_and_cancellation_release_guard(history, monkeypatch):
    import AI.summarize as summary

    append(history, "Текст")

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(*args, **kwargs):
            started.set()
            await release.wait()
            return "Сводка"

        provider = AsyncMock(side_effect=slow)
        monkeypatch.setattr(summary, "_generate_with_active_model", provider)
        task = asyncio.create_task(summary.summarize_chat_history(message(), None, history.log_path, ["typing"], catchup=True))
        await started.wait()
        duplicate = message()
        await summary.summarize_chat_history(duplicate, None, history.log_path, ["typing"], catchup=True)
        assert "уже готовится" in duplicate.reply.call_args.args[0]
        assert provider.call_count == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert history.summary_boundary(-1, 2)[1] is None
        release.set()
        assert await summary.summarize_chat_history(message(), None, history.log_path, ["typing"], catchup=True)

    asyncio.run(run())
