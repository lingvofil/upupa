import asyncio
from types import SimpleNamespace

from aiogram.enums import ContentType

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def _message(
    text: str | None,
    *,
    sender_is_bot: bool = False,
    content_type=ContentType.TEXT,
    reply_to_message=None,
):
    return SimpleNamespace(
        text=text,
        caption=None,
        content_type=content_type,
        chat=SimpleNamespace(id=12345, type="group"),
        from_user=SimpleNamespace(id=111, is_bot=sender_is_bot, first_name="OtherBot", full_name="OtherBot"),
        reply_to_message=reply_to_message,
        entities=None,
    )


def _bot_reply(*, text=None, caption=None, poll=None):
    return SimpleNamespace(
        text=text,
        caption=caption,
        poll=poll,
        from_user=SimpleNamespace(
            id=999,
            is_bot=True,
            first_name="Упупа",
            full_name="Упупа",
            username="upupa_bot",
        ),
        sender_chat=None,
        photo=None,
        video=None,
        animation=None,
        audio=None,
        voice=None,
        document=None,
        sticker=None,
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


def test_reply_to_analysis_result_is_added_as_immediate_context():
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
        captured["user_input"] = user_input
        return "follow-up"

    replied = _bot_reply(
        text="На фото старый красный автомобиль у моря, на заднем плане видны горы."
    )
    response = asyncio.run(
        generation.handle_bot_conversation(
            _message("а что там на заднем плане?", reply_to_message=replied),
            "Human",
            generate_response_func=fake_generate_response,
            needs_web_search_func=lambda _: False,
        )
    )

    assert response == "follow-up"
    assert captured["user_input"] == "а что там на заднем плане?"
    assert "Непосредственный контекст реплая" in captured["prompt"]
    assert "старый красный автомобиль у моря" in captured["prompt"]
    assert "главным локальным контекстом" in captured["prompt"]


def test_reply_to_holiday_digest_keeps_digest_context():
    from AI.dialog.generation import format_reply_context

    replied = _bot_reply(
        text="Праздники:\n🎉 День любителя странных носков\nСегодня разрешено выглядеть подозрительно."
    )
    context = format_reply_context(_message("а это настоящий праздник?", reply_to_message=replied))

    assert "Праздники:" in context
    assert "День любителя странных носков" in context
    assert "Автор сообщения: Упупа" in context


def test_reply_to_quiz_poll_includes_question_options_and_correct_answer():
    from AI.dialog.generation import format_reply_context

    poll = SimpleNamespace(
        question="Кто сказал: «я червяк»?",
        options=[
            SimpleNamespace(text="Вася"),
            SimpleNamespace(text="Петя"),
            SimpleNamespace(text="Упупа"),
        ],
        correct_option_id=2,
        explanation="",
    )
    replied = _bot_reply(poll=poll)

    context = format_reply_context(_message("почему упупа?", reply_to_message=replied))

    assert "Тип сообщения: викторина/опрос" in context
    assert "Кто сказал: «я червяк»?" in context
    assert "1. Вася; 2. Петя; 3. Упупа" in context
    assert "Правильный вариант: 3. Упупа" in context
