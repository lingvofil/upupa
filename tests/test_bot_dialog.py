import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def _message(text: str, *, sender_is_bot: bool = False):
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=12345),
        from_user=SimpleNamespace(is_bot=sender_is_bot),
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
