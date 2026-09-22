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


def test_participant_semantic_query_includes_reply_context(monkeypatch):
    from AI.dialog import generation

    generation.conversation_history.clear()
    generation.chat_settings["12345"] = {
        "dialog_enabled": True,
        "prompt": "participant prompt",
        "prompt_name": "Вася",
        "prompt_type": "user_style",
        "active_model": "gemini",
        "imitated_user": {"user_id": 42, "display_name": "Вася"},
    }

    captured = {}

    async def fake_prepare(chat_id, settings, query_text, *, current_message_text=None):
        captured["chat_id"] = chat_id
        captured["query_text"] = query_text
        captured["current_message_text"] = current_message_text
        return "", False

    async def fake_generate_response(prompt, chat_id, bot_name, user_input=""):
        return "reply"

    monkeypatch.setattr(generation, "prepare_participant_turn", fake_prepare)

    replied = _bot_reply(text="А муж че не пердит?")
    response = asyncio.run(
        generation.handle_bot_conversation(
            _message("еще как", reply_to_message=replied),
            "Human",
            generate_response_func=fake_generate_response,
            needs_web_search_func=lambda _: False,
        )
    )

    assert response == "reply"
    assert captured["current_message_text"] == "еще как"
    assert "еще как" in captured["query_text"]
    assert "А муж че не пердит?" in captured["query_text"]
    assert "Контекст сообщения, на которое отвечают" in captured["query_text"]


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



def test_participant_repetition_detector_catches_same_short_conversational_move():
    from AI.dialog.generation import _participant_reply_is_too_repetitive

    assert _participant_reply_is_too_repetitive(
        "обезян обезян обезян",
        ["что ты пристал еблан", "обезян обезян"],
    )
    assert not _participant_reply_is_too_repetitive(
        "у тебя батон",
        ["что ты пристал еблан", "обезян обезян"],
    )


def test_participant_prompt_marks_recent_persona_replies_as_do_not_repeat(monkeypatch):
    from AI.dialog import generation

    generation.conversation_history.clear()
    generation.conversation_history["12345"] = [
        {"role": "assistant", "name": "Six7ape", "content": "обезян обезян"},
    ]
    generation.chat_settings["12345"] = {
        "dialog_enabled": True,
        "prompt": "participant prompt",
        "prompt_name": "Six7ape",
        "prompt_type": "user_style",
        "active_model": "gemini",
        "imitated_user": {"user_id": 42, "display_name": "Six7ape"},
    }

    async def fake_prepare(*_args, **_kwargs):
        return "", False

    captured = {}

    async def fake_generate_response(prompt, chat_id, bot_name, user_input=""):
        captured["prompt"] = prompt
        return "новый ответ"

    monkeypatch.setattr(generation, "prepare_participant_turn", fake_prepare)

    response = asyncio.run(
        generation.handle_bot_conversation(
            _message("ну и че", reply_to_message=None),
            "Human",
            generate_response_func=fake_generate_response,
            needs_web_search_func=lambda _: False,
        )
    )

    assert response == "новый ответ"
    assert "[ANTI-REPETITION]" in captured["prompt"]
    assert "обезян обезян" in captured["prompt"]
    assert "не повторяй ту же фирменную фразу" in captured["prompt"]


def test_participant_generation_retries_once_on_repetitive_reply(monkeypatch):
    from AI.dialog import generation

    generation.conversation_history.clear()
    generation.conversation_history["12345"] = [
        {"role": "assistant", "name": "Six7ape", "content": "обезян обезян"},
    ]
    generation.chat_settings["12345"] = {
        "prompt_type": "user_style",
        "active_model": "gemini",
    }
    monkeypatch.setattr(generation, "update_chat_settings", lambda _chat_id: None)

    prompts = []
    replies = iter(["обезян обезян обезян", "да хуй знает"])

    def fake_generate_content(prompt, **_kwargs):
        prompts.append(prompt)
        return SimpleNamespace(text=next(replies))

    monkeypatch.setattr(
        generation,
        "model",
        SimpleNamespace(generate_content=fake_generate_content),
    )

    response = asyncio.run(
        generation.generate_response(
            "BASE PROMPT",
            "12345",
            "Six7ape",
            user_input="что будет",
        )
    )

    assert response == "да хуй знает"
    assert len(prompts) == 2
    assert "[ANTI-REPETITION RETRY]" in prompts[1]
    assert generation.conversation_history["12345"][-1]["content"] == "да хуй знает"



def test_pleading_trigger_uses_dedicated_generator_and_strips_command(monkeypatch):
    from AI.dialog import generation

    generation.conversation_history.clear()
    generation.chat_settings["12345"] = {
        "dialog_enabled": True,
        "prompt": "base prompt",
        "prompt_name": "упупа",
        "active_model": "groq",
    }

    captured = {}

    async def fake_pleading(prompt, chat_id, bot_name, user_input=""):
        captured["prompt"] = prompt
        captured["chat_id"] = chat_id
        captured["bot_name"] = bot_name
        captured["user_input"] = user_input
        return "premium reply"

    monkeypatch.setattr(generation, "generate_pleading_response", fake_pleading)

    response = asyncio.run(
        generation.handle_bot_conversation(
            _message("Упупа, умоляю, объясни квантовую механику"),
            "Human",
            needs_web_search_func=lambda _: False,
        )
    )

    assert response == "premium reply"
    assert captured["user_input"] == "объясни квантовую механику"
    assert captured["chat_id"] == "12345"
    assert generation.conversation_history["12345"][-1] == {
        "role": "user",
        "name": "Human",
        "content": "объясни квантовую механику",
    }


def test_pleading_response_forces_gemini_and_uses_pleading_queue(monkeypatch):
    from AI.dialog import generation
    from core.settings import MODEL_QUEUE_PLEADING

    generation.conversation_history.clear()
    generation.chat_settings["12345"] = {
        "prompt_type": "standard",
        "active_model": "groq",
    }
    monkeypatch.setattr(generation, "update_chat_settings", lambda _chat_id: None)

    captured = {}

    def fake_generate_content(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return SimpleNamespace(text="ответ 3.8")

    def fail_groq(*_args, **_kwargs):
        raise AssertionError("pleading route must not use active groq model")

    monkeypatch.setattr(
        generation,
        "model",
        SimpleNamespace(generate_content=fake_generate_content),
    )
    monkeypatch.setattr(
        generation.groq_ai,
        "generate_text",
        fail_groq,
    )

    response = asyncio.run(
        generation.generate_pleading_response(
            "PREMIUM PROMPT",
            "12345",
            "Упупа",
            user_input="сложный вопрос",
        )
    )

    assert response == "ответ 3.8"
    assert captured["prompt"] == "PREMIUM PROMPT"
    assert captured["kwargs"]["chat_id"] == 12345
    assert captured["kwargs"]["model_queue"] == MODEL_QUEUE_PLEADING
