import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.types import Message, Update
import pytest

from features import dialogue_video as feature


NOW = datetime(2026, 11, 25, 18, tzinfo=timezone.utc)


def message(index, text, *, topic=11, chat=-10001, author=1, date=NOW, protected=False):
    return Message.model_validate({
        "message_id": index,
        "date": date,
        "chat": {"id": chat, "type": "supergroup"},
        "from": {"id": author, "is_bot": False, "first_name": f"Участник {author}"},
        "message_thread_id": topic,
        "is_topic_message": True,
        "text": text,
        "has_protected_content": protected,
    })


@pytest.fixture(autouse=True)
def isolated_history(monkeypatch):
    monkeypatch.setattr(feature, "_history", feature.OrderedDict())
    monkeypatch.setattr(feature, "_last_video", feature.OrderedDict())
    monkeypatch.setattr(feature, "_active_chats", set())


def test_manual_command_routes_through_middleware_and_sends_only_current_topic(monkeypatch):
    from tests import test_smoke_imports  # noqa: F401  (env + mocks)
    from handlers.dialog import dialogue_video_command, router as dialog_router
    from aiogram import Router

    calls = AsyncMock()
    monkeypatch.setattr(Bot, "__call__", calls)
    calls.return_value = None
    rendered = []

    async def render(lines, directory):
        rendered.extend(line.text for line in lines)
        output = directory / "dialogue.mp4"
        output.write_bytes(b"video")
        return output

    monkeypatch.setattr(feature, "render_dialogue_video", render)

    async def run():
        dispatcher = Dispatcher()
        router = Router()
        router.message.handlers.extend(
            handler for handler in dialog_router.message.handlers if handler.callback is dialogue_video_command
        )
        dispatcher.include_router(router)
        dispatcher.message.outer_middleware(feature.DialogueVideoHistoryMiddleware())
        async with Bot("123456:dialogue-test") as bot:
            updates = [
                message(1, "Давно", date=NOW - timedelta(minutes=31)),
                message(2, "Соседняя тема", topic=22),
                message(3, "Другой чат", chat=-10002),
                message(4, "Закрытое сообщение", protected=True),
                message(5, "Первая реплика"),
                message(6, "Вторая реплика", author=2),
                message(7, "Упупа, диалог"),
                message(8, "/dialog"),
            ]
            for event in updates:
                await dispatcher.feed_update(bot, Update(update_id=event.message_id, message=event))

    asyncio.run(run())
    assert rendered == ["Первая реплика", "Вторая реплика"]
    assert len(calls.await_args_list) == 2
    video, cooldown = [call.args[0] for call in calls.await_args_list]
    assert video.__api_method__ == "sendVideo"
    assert video.chat_id == -10001
    assert video.message_thread_id == 11
    assert video.protect_content and video.supports_streaming
    assert "ещё готовится" in cooldown.text


def test_automatic_trigger_has_a_separate_longer_chat_cooldown(monkeypatch):
    calls = AsyncMock()
    monkeypatch.setattr(Bot, "__call__", calls)
    monkeypatch.setattr(feature.random, "random", lambda: 0)
    now = [1000]
    monkeypatch.setattr(feature, "monotonic", lambda: now[0])
    monkeypatch.setattr(feature, "render_dialogue_video", AsyncMock(return_value="dialogue.mp4"))

    async def run():
        async with Bot("123456:dialogue-test") as bot:
            for index in range(1, 5):
                event = message(index, f"Реплика {index}", author=index % 2 + 1).as_(bot)
                feature._record(event)
                assert await feature.send_dialogue_video(event, automatic=True) is (index == 4)
            now[0] += feature.MANUAL_COOLDOWN
            assert not await feature.send_dialogue_video(event, automatic=True)
            now[0] += feature.RANDOM_COOLDOWN
            assert await feature.send_dialogue_video(event, automatic=True)
            assert calls.await_count == 2

    asyncio.run(run())
