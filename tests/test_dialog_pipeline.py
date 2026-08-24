import asyncio
from types import SimpleNamespace

from aiogram.enums import ContentType

from tests import test_smoke_imports  # noqa: F401  (env + mocks)


def _message(message_id=101, text="голубь", *, sender_is_bot=False):
    sent = []
    replies = []

    async def send_message(chat_id, value, **kwargs):
        sent.append((chat_id, value, kwargs))

    async def reply(value):
        replies.append(value)

    message = SimpleNamespace(
        message_id=message_id,
        text=text,
        caption=None,
        content_type=ContentType.TEXT,
        chat=SimpleNamespace(id=77, type="group", title="Test chat", username=None),
        from_user=SimpleNamespace(
            id=123,
            is_bot=sender_is_bot,
            first_name="Детектор",
            full_name="Детектор",
            username="detector",
        ),
        reply_to_message=None,
        entities=None,
        voice=None,
        bot=SimpleNamespace(send_message=send_message),
        reply=reply,
    )
    return message, sent, replies


def test_pipeline_runs_reactions_once_then_dialog(monkeypatch):
    from features import dialog_pipeline as pipeline

    calls = []

    async def fake_reactions(message):
        calls.append("reactions")
        return False

    async def fake_dialog(message):
        calls.append("dialog")

    monkeypatch.setattr(pipeline, "process_random_reactions_once", fake_reactions)
    monkeypatch.setattr(pipeline, "process_general_dialog_message", fake_dialog)

    message, _, _ = _message()
    consumed = asyncio.run(pipeline.process_dialog_pipeline(message))

    assert consumed is False
    assert calls == ["reactions", "dialog"]


def test_pipeline_stops_before_dialog_when_reaction_consumes_message(monkeypatch):
    from features import dialog_pipeline as pipeline

    calls = []

    async def fake_reactions(message):
        calls.append("reactions")
        return True

    async def fake_dialog(message):
        calls.append("dialog")

    monkeypatch.setattr(pipeline, "process_random_reactions_once", fake_reactions)
    monkeypatch.setattr(pipeline, "process_general_dialog_message", fake_dialog)

    message, _, _ = _message()
    consumed = asyncio.run(pipeline.process_dialog_pipeline(message))

    assert consumed is True
    assert calls == ["reactions"]


def test_reaction_stage_uses_live_context_without_runtime_patch(monkeypatch):
    from features import dialog_pipeline as pipeline

    pipeline.situational_summary._recent_chat_messages.clear()
    pipeline.situational_summary._seen_message_ids.clear()
    pipeline.chat_settings["77"] = {
        "dialog_enabled": True,
        "reactions_enabled": True,
        "emoji_enabled": False,
        "ai_prob": 1.0,
    }

    accounting = []
    captured = {}

    async def fake_save(message):
        accounting.append("save")

    async def fake_track(message):
        accounting.append("stats")

    def fake_add_chat(*args):
        accounting.append("chat")

    async def fake_live_reaction(chat_id):
        captured["history"] = list(pipeline.situational_summary._context_for_chat(chat_id))
        return "*происходит голубь*"

    monkeypatch.setattr(pipeline, "save_user_message", fake_save)
    monkeypatch.setattr(pipeline, "track_message_statistics", fake_track)
    monkeypatch.setattr(pipeline, "add_chat", fake_add_chat)
    monkeypatch.setattr(pipeline, "_generate_live_situational_reaction", fake_live_reaction)
    monkeypatch.setattr(pipeline.random_reactions.random, "random", lambda: 0.0)

    message, sent, _ = _message(text="в чат прилетел голубь")
    consumed = asyncio.run(pipeline.process_random_reactions_once(message))

    assert consumed is True
    assert accounting == ["save", "stats", "chat"]
    assert captured["history"][-1]["content"] == "в чат прилетел голубь"
    assert sent == [(77, "*происходит голубь*", {"parse_mode": "Markdown"})]


def test_reaction_stage_is_idempotent_by_message_id(monkeypatch):
    from features import dialog_pipeline as pipeline

    pipeline.situational_summary._recent_chat_messages.clear()
    pipeline.situational_summary._seen_message_ids.clear()
    pipeline.chat_settings["77"] = {
        "dialog_enabled": True,
        "reactions_enabled": False,
        "emoji_enabled": False,
    }

    calls = []

    async def fake_save(message):
        calls.append("save")

    async def fake_track(message):
        calls.append("stats")

    def fake_add_chat(*args):
        calls.append("chat")

    monkeypatch.setattr(pipeline, "save_user_message", fake_save)
    monkeypatch.setattr(pipeline, "track_message_statistics", fake_track)
    monkeypatch.setattr(pipeline, "add_chat", fake_add_chat)

    message, _, _ = _message(message_id=555)
    first = asyncio.run(pipeline.process_random_reactions_once(message))
    second = asyncio.run(pipeline.process_random_reactions_once(message))

    assert first is False
    assert second is False
    assert calls == ["save", "stats", "chat"]


def test_general_dialog_stage_handles_unknown_bot_message(monkeypatch):
    from features import dialog_pipeline as pipeline

    pipeline.chat_settings["77"] = {
        "dialog_enabled": True,
        "prompt": "base",
        "prompt_name": "упупа",
        "active_model": "gemini",
    }
    captured = {}

    async def fake_serious(message):
        return False

    async def fake_send_chat_action(chat_id, action):
        captured["chat_action"] = (chat_id, action)

    async def fake_conversation(message, user_first_name):
        captured["user_first_name"] = user_first_name
        return "generated"

    monkeypatch.setattr(pipeline, "handle_serious_mode_reply", fake_serious)
    monkeypatch.setattr(pipeline, "update_chat_settings", lambda _: None)
    monkeypatch.setattr(pipeline, "handle_bot_conversation", fake_conversation)
    monkeypatch.setattr(
        pipeline,
        "bot",
        SimpleNamespace(id=999, send_chat_action=fake_send_chat_action),
    )

    message, _, replies = _message(text=None, sender_is_bot=True)
    message.content_type = ContentType.UNKNOWN

    asyncio.run(pipeline.process_general_dialog_message(message))

    assert replies == ["generated"]
    assert captured["user_first_name"] == "Детектор"
    assert captured["chat_action"] == ("77", "typing")
