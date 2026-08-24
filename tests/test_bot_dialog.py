import asyncio
from types import SimpleNamespace

from aiogram.enums import ContentType

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def _message(text: str | None, *, sender_is_bot: bool = False, content_type=ContentType.TEXT):
    return SimpleNamespace(
        text=text,
        caption=None,
        content_type=content_type,
        chat=SimpleNamespace(id=12345, type="group"),
        from_user=SimpleNamespace(id=111, is_bot=sender_is_bot, first_name="OtherBot", full_name="OtherBot"),
        reply_to_message=None,
        entities=None,
    )


def test_empty_human_trigger_keeps_short_reply():
    from AI.dialog.generation import handle_bot_conversation

    response = asyncio.run(handle_bot_conversation(_message("упупа"), "Human"))

    assert response == "Хули?"


def test_empty_bot_trigger_uses_full_dialog_generation():
    from AI.dialog import generation

    generation.conversation_history.clear()
    generation.chat_settings["12345"] = {
        "dialog_enabled": True,
        "prompt": "base prompt",
        "prompt_name": "упупа",
        "active_model": "gemini",
    }

    captured = {}

    async def fake_generate_response(prompt, chat_id, bot_name, user_input=""):
        captured["prompt"] = prompt
        captured["chat_id"] = chat_id
        captured["bot_name"] = bot_name
        captured["user_input"] = user_input
        return "generated dialog reply"

    response = asyncio.run(
        generation.handle_bot_conversation(
            _message("упупа", sender_is_bot=True),
            "OtherBot",
            generate_response_func=fake_generate_response,
            needs_web_search_func=lambda _: False,
        )
    )

    assert response == "generated dialog reply"
    assert captured["user_input"] == "упупа"
    assert "Не упоминай процент уверенности" in captured["prompt"]
    assert generation.conversation_history["12345"][-1] == {
        "role": "user",
        "name": "OtherBot",
        "content": "упупа",
    }


def test_confidence_percentage_sanitizer_removes_common_forms():
    from AI.response_sanitizer import strip_confidence_percentages

    text = (
        "Уверенность: 82%.\n"
        "Я уверен на 70%, что это роутер.\n"
        "Проверь питание и кабель. (уверенность: 65%)"
    )

    assert strip_confidence_percentages(text) == "это роутер.\nПроверь питание и кабель."


def test_unknown_bot_message_uses_full_dialog_generation():
    from AI.dialog import generation

    generation.conversation_history.clear()
    generation.chat_settings["12345"] = {
        "dialog_enabled": True,
        "prompt": "base prompt",
        "prompt_name": "упупа",
        "active_model": "gemini",
    }

    captured = {}

    async def fake_generate_response(prompt, chat_id, bot_name, user_input=""):
        captured["user_input"] = user_input
        return "generated dialog reply"

    response = asyncio.run(
        generation.handle_bot_conversation(
            _message(None, sender_is_bot=True, content_type=ContentType.UNKNOWN),
            "OtherBot",
            generate_response_func=fake_generate_response,
            needs_web_search_func=lambda _: False,
        )
    )

    assert response == "generated dialog reply"
    assert captured["user_input"] == "[сообщение другого бота без доступного текста]"
