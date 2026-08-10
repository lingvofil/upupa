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
    from AI.talking import handle_bot_conversation

    response = asyncio.run(handle_bot_conversation(_message("упупа"), "Human"))

    assert response == "Хули?"


def test_empty_bot_trigger_uses_full_dialog_generation(monkeypatch):
    from AI import talking

    talking.conversation_history.clear()
    talking.chat_settings["12345"] = {
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

    monkeypatch.setattr(talking, "needs_web_search", lambda _: False)
    monkeypatch.setattr(talking, "generate_response", fake_generate_response)

    response = asyncio.run(talking.handle_bot_conversation(_message("упупа", sender_is_bot=True), "OtherBot"))

    assert response == "generated dialog reply"
    assert captured["user_input"] == "упупа"
    assert talking.conversation_history["12345"][-1] == {
        "role": "user",
        "name": "OtherBot",
        "content": "упупа",
    }


def test_unknown_bot_message_uses_full_dialog_generation(monkeypatch):
    from AI import talking

    talking.conversation_history.clear()
    talking.chat_settings["12345"] = {
        "dialog_enabled": True,
        "prompt": "base prompt",
        "prompt_name": "упупа",
        "active_model": "gemini",
    }

    replies = []
    captured = {}

    async def fake_send_chat_action(chat_id, action):
        captured["chat_action"] = (chat_id, action)

    async def fake_generate_response(prompt, chat_id, bot_name, user_input=""):
        captured["user_input"] = user_input
        return "generated dialog reply"

    async def fake_reply(text):
        replies.append(text)

    message = _message(None, sender_is_bot=True, content_type=ContentType.UNKNOWN)
    message.reply = fake_reply

    monkeypatch.setattr(talking, "bot", SimpleNamespace(id=999, send_chat_action=fake_send_chat_action))
    monkeypatch.setattr(talking, "needs_web_search", lambda _: False)
    monkeypatch.setattr(talking, "generate_response", fake_generate_response)

    asyncio.run(talking.process_general_message(message))

    assert replies == ["generated dialog reply"]
    assert captured["user_input"] == "[сообщение другого бота без доступного текста]"
