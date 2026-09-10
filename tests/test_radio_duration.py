import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import test_smoke_imports  # noqa: F401


def test_radio_duration_parser_and_keyboard():
    import handlers.radio as radio_handler

    assert radio_handler.parse_radio_request("радио упупы") == (True, None)
    assert radio_handler.parse_radio_request("Упупа, радио 3") == (True, 3)
    assert radio_handler.parse_radio_request("радио упупы 5") == (True, 5)
    assert radio_handler.parse_radio_request("радио упупы 2") == (True, 2)
    assert radio_handler.parse_radio_request("радио упупы пожалуйста") == (False, None)

    markup = radio_handler.get_radio_duration_markup()
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert [button.text for button in buttons] == ["1 мин", "3 мин", "5 мин"]
    assert [button.callback_data for button in buttons] == [
        "radio:duration:1",
        "radio:duration:3",
        "radio:duration:5",
    ]


def test_bare_radio_command_asks_for_duration(monkeypatch):
    import handlers.radio as radio_handler

    build = AsyncMock()
    monkeypatch.setattr(radio_handler, "build_radio_episode", build)

    reply = AsyncMock()
    message = SimpleNamespace(
        text="радио упупы",
        chat=SimpleNamespace(id=-1001),
        from_user=SimpleNamespace(id=42),
        reply=reply,
    )

    asyncio.run(radio_handler.handle_radio_command(message))

    build.assert_not_awaited()
    reply.assert_awaited_once()
    assert reply.await_args.args[0] == "📻 Скока вещаем?"
    markup = reply.await_args.kwargs["reply_markup"]
    assert len(markup.inline_keyboard[0]) == 3


def test_direct_radio_duration_reaches_episode_builder(monkeypatch):
    import handlers.radio as radio_handler

    episode = SimpleNamespace(
        audio=b"mp3",
        message_count=12,
        word_count=650,
        tts_provider="gemini",
        tts_chunks=2,
    )
    build = AsyncMock(return_value=episode)
    monkeypatch.setattr(radio_handler, "build_radio_episode", build)

    status = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    bot = SimpleNamespace(send_chat_action=AsyncMock(), send_voice=AsyncMock())
    message = SimpleNamespace(
        text="радио упупы 5",
        chat=SimpleNamespace(id=-1001),
        from_user=SimpleNamespace(id=42),
        message_id=555,
        bot=bot,
        reply=AsyncMock(return_value=status),
    )

    asyncio.run(radio_handler.handle_radio_command(message))

    build.assert_awaited_once_with("-1001", duration_minutes=5)
    bot.send_voice.assert_awaited_once()
    assert bot.send_voice.await_args.kwargs["reply_to_message_id"] == 555


def test_invalid_direct_duration_is_rejected(monkeypatch):
    import handlers.radio as radio_handler

    build = AsyncMock()
    monkeypatch.setattr(radio_handler, "build_radio_episode", build)
    reply = AsyncMock()
    message = SimpleNamespace(
        text="радио упупы 2",
        chat=SimpleNamespace(id=-1001),
        from_user=SimpleNamespace(id=42),
        reply=reply,
    )

    asyncio.run(radio_handler.handle_radio_command(message))

    build.assert_not_awaited()
    assert "1, 3, 5" in reply.await_args.args[0]


def test_script_duration_changes_prompt_and_hard_limit(monkeypatch):
    import features.radio.script as radio_script

    prompts = []

    async def fake_generate(prompt, chat_id, **kwargs):
        prompts.append(prompt)
        return " ".join(["слово"] * 300)

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
            duration_minutes=1,
        )
    )

    assert len(result.text.split()) <= 160
    assert "примерно 1 мин" in prompts[0]
    assert "100–140" in prompts[0]
    assert "160 слов" in prompts[0]
    assert radio_script.get_radio_word_targets(5) == (580, 720, 760)
    with pytest.raises(ValueError):
        radio_script.get_radio_word_targets(2)
