import asyncio
from types import SimpleNamespace

from aiogram.enums import ContentType

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def _bot_reply(text: str):
    return SimpleNamespace(
        text=text,
        caption=None,
        poll=None,
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


def _message(text: str, *, replied, quote=None):
    return SimpleNamespace(
        text=text,
        caption=None,
        content_type=ContentType.TEXT,
        chat=SimpleNamespace(id=12345, type="group"),
        from_user=SimpleNamespace(
            id=111,
            is_bot=False,
            first_name="Human",
            full_name="Human",
        ),
        reply_to_message=replied,
        quote=quote,
        entities=None,
        bot=SimpleNamespace(id=999),
    )


def test_manual_quote_reply_marks_selected_fragment_as_primary_context():
    from AI.dialog.generation import format_reply_context

    replied = _bot_reply(
        "Первый абзац про погоду.\n"
        "Артефакт нельзя передать другому игроку.\n"
        "Последний абзац про правила."
    )
    message = _message(
        "а почему?",
        replied=replied,
        quote=SimpleNamespace(
            text="Артефакт нельзя передать другому игроку.",
            is_manual=True,
        ),
    )

    context = format_reply_context(message)

    assert "Telegram Quote & Reply" in context
    assert "Артефакт нельзя передать другому игроку." in context
    assert "Это главный предмет текущего ответа" in context
    assert "Полный текст исходного сообщения (фон)" in context
    assert context.index("Telegram Quote & Reply") < context.index("Полный текст исходного сообщения (фон)")


def test_regular_reply_keeps_whole_message_as_reply_context():
    from AI.dialog.generation import format_reply_context

    replied = _bot_reply("Обычный ответ бота целиком.")
    context = format_reply_context(_message("уточни", replied=replied))

    assert "Текст сообщения:\nОбычный ответ бота целиком." in context
    assert "Telegram Quote & Reply" not in context
    assert "Полный текст исходного сообщения (фон)" not in context


def test_quote_reply_prompt_explicitly_prioritizes_quote_over_full_reply():
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
        "Начало большого ответа.\n"
        "Вот конкретная фраза, которую пользователь выделил.\n"
        "Продолжение большого ответа."
    )
    message = _message(
        "это точно?",
        replied=replied,
        quote=SimpleNamespace(
            text="Вот конкретная фраза, которую пользователь выделил.",
            is_manual=True,
        ),
    )

    response = asyncio.run(
        generation.handle_bot_conversation(
            message,
            "Human",
            generate_response_func=fake_generate_response,
            needs_web_search_func=lambda _: False,
        )
    )

    assert response == "follow-up"
    assert captured["user_input"] == "это точно?"
    assert "Вот конкретная фраза, которую пользователь выделил." in captured["prompt"]
    assert "считай прежде всего её главным предметом текущего вопроса" in captured["prompt"]
    assert "полный текст исходного сообщения используй как фон" in captured["prompt"]
