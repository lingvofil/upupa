import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock
import wave

from aiogram import Bot, Dispatcher
from aiogram.types import Message, Update
import pytest

from features import dialogue_video as feature
from infrastructure.subprocess_runner import run_subprocess
from services import dialogue_video as renderer


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

    async def render(lines, directory, *, chat_title):
        rendered.extend((line.text, line.reply_to_id) for line in lines)
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
            first = message(5, "Первая реплика")
            second = message(6, "Вторая реплика", author=2).model_copy(update={"reply_to_message": first})
            updates = [
                message(1, "Давно", date=NOW - timedelta(minutes=31)),
                message(2, "Соседняя тема", topic=22),
                message(3, "Другой чат", chat=-10002),
                message(4, "Закрытое сообщение", protected=True),
                first,
                second,
                message(7, "Упупа, диалог"),
                message(8, "/dialog"),
            ]
            for event in updates:
                await dispatcher.feed_update(bot, Update(update_id=event.message_id, message=event))

    asyncio.run(run())
    assert rendered == [("Давно", None), ("Первая реплика", None), ("Вторая реплика", 5)]
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


def test_admin_can_save_dialogue_chance_and_change_only_automatic_trigger(monkeypatch, tmp_path):
    from tests import test_smoke_imports  # noqa: F401  (env + mocks)
    from core.json_repository import JsonFileRepository
    from features import chat_settings as settings_store, interactive_settings as menu

    monkeypatch.setitem(feature.chat_settings, "-10001", {})
    repository = JsonFileRepository(tmp_path / "chat_settings.json")
    monkeypatch.setattr(menu, "save_chat_settings", lambda: settings_store.save_chat_settings(repository))
    monkeypatch.setattr(menu, "has_settings_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(Bot, "__call__", AsyncMock())
    monkeypatch.setattr(feature.random, "random", lambda: 0.9)
    render = AsyncMock(return_value="dialogue.mp4")
    monkeypatch.setattr(feature, "render_dialogue_video", render)

    query = SimpleNamespace(
        message=SimpleNamespace(chat=SimpleNamespace(id=-10001), edit_text=AsyncMock()),
        from_user=SimpleNamespace(id=42),
        answer=AsyncMock(),
    )

    async def press(callback):
        query.data = callback
        await menu.handle_settings_callback(query)
        markup = query.message.edit_text.call_args.kwargs["reply_markup"]
        return {button.callback_data: button.text for row in markup.inline_keyboard for button in row}

    async def run():
        prob_type = f"settings:prob_type:{feature.PROBABILITY_KEY}"
        set_prob = f"settings:set_prob:{feature.PROBABILITY_KEY}"
        assert (await press("settings:view:probs_menu"))[prob_type] == "🎬 Диалог (0.1%)"
        assert (await press(prob_type))[f"{set_prob}:0"] == "0% (Выкл)"
        assert (await press(f"{set_prob}:0"))[prob_type] == "🎬 Диалог (0%)"
        assert repository.load()["-10001"][feature.PROBABILITY_KEY] == 0

        async with Bot("123456:dialogue-test") as bot:
            for index in range(1, 5):
                event = message(index, f"Реплика {index}", author=index % 2 + 1).as_(bot)
                feature._record(event)
            assert not await feature.send_dialogue_video(event, automatic=True)
            assert await feature.send_dialogue_video(message(5, "диалог").as_(bot))

            feature._last_video.clear()
            assert (await press(f"{set_prob}:1.0"))[prob_type] == "🎬 Диалог (100%)"
            assert repository.load()["-10001"][feature.PROBABILITY_KEY] == 1
            assert await feature.send_dialogue_video(event, automatic=True)

    asyncio.run(run())
    assert render.await_count == 2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
def test_voiced_video_keeps_last_bubble_on_screen_until_audio_ends(monkeypatch, tmp_path):
    wav = BytesIO()
    with wave.open(wav, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 48000)
    gemini = AsyncMock(return_value=wav.getvalue())
    fallback = AsyncMock()
    monkeypatch.setattr(renderer, "synthesize_gemini_wav", gemini)
    monkeypatch.setattr(renderer, "_espeak_utterances", fallback)

    async def run():
        lines = [
            renderer.Line(1, 1, "Лена", "Первая", NOW),
            renderer.Line(2, 2, "Илья", "Вторая", NOW),
            renderer.Line(3, 1, "Лена", "Последняя", NOW),
        ]
        video = await renderer.render_dialogue_video(lines, tmp_path, chat_title="Чат")
        frame = await run_subprocess(
            ["ffmpeg", "-v", "error", "-ss", "7.4", "-i", str(video),
             "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
            timeout=10,
        )
        assert frame.ok and frame.stdout.startswith(b"\x89PNG")

    asyncio.run(run())
    profiles = [call.kwargs["voice_name"] for call in gemini.await_args_list]
    assert len(profiles) == 3 and profiles[0] == profiles[2] != profiles[1]
    assert gemini.await_args_list[0].args[0].endswith('"Первая"')
    fallback.assert_not_awaited()


def test_gemini_error_falls_back_for_the_entire_clip(monkeypatch, tmp_path):
    lines = [renderer.Line(1, 1, "Лена", "Первая", NOW)]
    gemini = AsyncMock(side_effect=renderer.SpeechSynthesisError("quota"))
    local = AsyncMock(return_value=[])
    monkeypatch.setattr(renderer, "synthesize_gemini_wav", gemini)
    monkeypatch.setattr(renderer, "_espeak_utterances", local)

    assert asyncio.run(renderer._voice(lines, tmp_path)) is None
    local.assert_awaited_once_with(lines, tmp_path)
