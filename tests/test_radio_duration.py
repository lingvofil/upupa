import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import test_smoke_imports  # noqa: F401


def test_radio_command_uses_default_duration_without_selector(monkeypatch):
    import handlers.radio as radio_handler

    assert radio_handler.is_radio_command("радио упупы")
    assert radio_handler.is_radio_command("радио упупа")
    assert radio_handler.is_radio_command("Упупа, радио")
    assert not radio_handler.is_radio_command("радио упупы 1")
    assert not radio_handler.is_radio_command("радио упупа 3")
    assert not hasattr(radio_handler, "get_radio_duration_markup")
    assert not hasattr(radio_handler, "handle_radio_duration_callback")

    episode = SimpleNamespace(
        audio=b"mp3",
        requested_duration_minutes=3,
        message_count=12,
        word_count=360,
        tts_provider="gemini",
        tts_chunks=1,
    )
    build = AsyncMock(return_value=episode)
    monkeypatch.setattr(radio_handler, "build_radio_episode", build)

    status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    bot = SimpleNamespace(send_chat_action=AsyncMock(), send_voice=AsyncMock())
    message = SimpleNamespace(
        text="радио упупа",
        chat=SimpleNamespace(id=-1001),
        from_user=SimpleNamespace(id=42),
        message_id=555,
        bot=bot,
        reply=AsyncMock(return_value=status),
    )

    asyncio.run(radio_handler.handle_radio_command(message))

    build.assert_awaited_once_with("-1001")
    bot.send_voice.assert_awaited_once()
    assert bot.send_voice.await_args.kwargs["reply_to_message_id"] == 555


def test_default_script_duration_changes_prompt_and_hard_limit(monkeypatch):
    import features.radio.script as radio_script

    prompts = []

    async def fake_generate(prompt, chat_id, **kwargs):
        prompts.append(prompt)
        return " ".join(["слово"] * 360)

    monkeypatch.setattr(radio_script, "_generate_with_active_model", fake_generate)
    messages = [
        {"display_name": "Вася", "username": "vasya", "text": "Обсуждали арбуз и лёд."},
        {"display_name": "Петя", "username": "petya", "text": "Потом спорили про коктейль."},
    ]

    result = asyncio.run(
        radio_script.generate_radio_script(
            "-1001",
            "Чятище",
            messages,
            24,
        )
    )

    assert radio_script.RADIO_DEFAULT_DURATION_MINUTES == 3
    assert len(result.text.split()) <= radio_script.RADIO_MAX_WORDS
    assert "примерно 3 мин" in prompts[0]
    assert "330–480" in prompts[0]
    assert f"{radio_script.RADIO_MAX_WORDS} слов" in prompts[0]
    with pytest.raises(ValueError):
        radio_script.get_radio_word_targets(2)


def test_short_script_is_regenerated_until_default_duration_floor(monkeypatch):
    import features.radio.script as radio_script

    prompts = []
    outputs = [
        " ".join(["коротко"] * 120),
        " ".join(["нормально"] * 360),
    ]

    async def fake_generate(prompt, chat_id, **kwargs):
        prompts.append(prompt)
        return outputs.pop(0)

    monkeypatch.setattr(radio_script, "_generate_with_active_model", fake_generate)
    messages = [
        {"display_name": "Вася", "username": "vasya", "text": "Обсуждали арбуз и лёд."},
        {"display_name": "Петя", "username": "petya", "text": "Потом спорили про коктейль."},
    ]

    result = asyncio.run(
        radio_script.generate_radio_script(
            "-1001",
            "Чятище",
            messages,
            24,
        )
    )

    assert result.word_count == 360
    assert len(prompts) == 2
    assert "не опускайся ниже 330 слов" in prompts[0].lower()
    assert "предыдущая попытка получилась всего на 120 слов" in prompts[1].lower()
    assert "перепиши сценарий полностью" in prompts[1].lower()


def test_persistently_short_default_script_fails_instead_of_sending_tiny_episode(monkeypatch):
    import features.radio.script as radio_script

    calls = 0

    async def fake_generate(prompt, chat_id, **kwargs):
        nonlocal calls
        calls += 1
        return " ".join(["коротко"] * 120)

    monkeypatch.setattr(radio_script, "_generate_with_active_model", fake_generate)
    messages = [
        {"display_name": "Вася", "username": "vasya", "text": "Обсуждали арбуз и лёд."},
        {"display_name": "Петя", "username": "petya", "text": "Потом спорили про коктейль."},
    ]

    with pytest.raises(RuntimeError, match="remained too short"):
        asyncio.run(
            radio_script.generate_radio_script(
                "-1001",
                "Чятище",
                messages,
                24,
            )
        )

    assert calls == radio_script.RADIO_MIN_LENGTH_ATTEMPTS
