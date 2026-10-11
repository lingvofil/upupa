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

    def fake_participant_cache(message):
        accounting.append("participant-cache")

    async def fake_track(message):
        accounting.append("stats")

    def fake_add_chat(*args):
        accounting.append("chat")

    async def fake_live_reaction(chat_id):
        captured["history"] = list(pipeline.situational_summary._context_for_chat(chat_id))
        return "*происходит голубь*"

    monkeypatch.setattr(pipeline, "save_user_message", fake_save)
    monkeypatch.setattr(pipeline, "record_participant_message", fake_participant_cache)
    monkeypatch.setattr(pipeline, "track_message_statistics", fake_track)
    monkeypatch.setattr(pipeline, "add_chat", fake_add_chat)
    monkeypatch.setattr(pipeline, "_generate_live_situational_reaction", fake_live_reaction)
    monkeypatch.setattr(pipeline.random_reactions.random, "random", lambda: 0.0)

    message, sent, _ = _message(text="в чат прилетел голубь")
    consumed = asyncio.run(pipeline.process_random_reactions_once(message))

    assert consumed is True
    assert accounting == ["save", "participant-cache", "stats", "chat"]
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

    def fake_participant_cache(message):
        calls.append("participant-cache")

    async def fake_track(message):
        calls.append("stats")

    def fake_add_chat(*args):
        calls.append("chat")

    monkeypatch.setattr(pipeline, "save_user_message", fake_save)
    monkeypatch.setattr(pipeline, "record_participant_message", fake_participant_cache)
    monkeypatch.setattr(pipeline, "track_message_statistics", fake_track)
    monkeypatch.setattr(pipeline, "add_chat", fake_add_chat)

    message, _, _ = _message(message_id=555)
    first = asyncio.run(pipeline.process_random_reactions_once(message))
    second = asyncio.run(pipeline.process_random_reactions_once(message))

    assert first is False
    assert second is False
    assert calls == ["save", "participant-cache", "stats", "chat"]


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


def test_split_participant_series_caps_parts_and_keeps_code():
    from AI.dialog.delivery import split_participant_series

    assert split_participant_series("одно сообщение") == ["одно сообщение"]
    assert split_participant_series("раз\n\nдва\n три ") == ["раз", "два", "три"]
    assert split_participant_series("\n".join(str(i) for i in range(10)), max_parts=3) == [
        "0",
        "1",
        "2\n3\n4\n5\n6\n7\n8\n9",
    ]
    assert split_participant_series("```\na\nb\n```") == ["```\na\nb\n```"]
    assert split_participant_series("   ") == []


def test_participant_reply_is_sent_as_series_with_human_pauses():
    import random

    from AI.dialog.delivery import send_dialog_response

    message, _, replies = _message()
    events = []

    async def send_chat_action(chat_id, action):
        events.append(("action", chat_id, action))

    async def send_message(chat_id, text):
        events.append(("send", chat_id, text))

    async def fake_sleep(seconds):
        events.append(("sleep", seconds))

    fake_bot = SimpleNamespace(send_chat_action=send_chat_action, send_message=send_message)
    second = "я вчера туда сходил, там вообще всё не так как обещали"

    asyncio.run(
        send_dialog_response(
            message,
            f"короче слушай\n{second}\nну ты понял",
            settings={
                "prompt_type": "user_style",
                "style_series_timing": {"rates": [2.0], "min_gap": 3.0},
            },
            bot=fake_bot,
            sleep=fake_sleep,
            rng=random.Random(1),
        )
    )

    assert replies == ["короче слушай"]
    sends = [event[2] for event in events if event[0] == "send"]
    assert sends == [second, "ну ты понял"]

    # Pause before each part ≈ its length / the participant's 2 chars/s,
    # with "typing…" refreshed so it never fades during a long pause.
    waits, current = [], 0.0
    for event in events:
        if event[0] == "sleep":
            assert event[1] <= 4.5
            current += event[1]
        elif event[0] == "send":
            waits.append(current)
            current = 0.0
    assert len(second) / 2.0 * 0.85 <= waits[0] <= len(second) / 2.0 * 1.15
    assert 11 / 2.0 * 0.85 <= waits[1] <= 11 / 2.0 * 1.15
    actions = sum(1 for event in events if event[0] == "action")
    assert actions >= 7


def test_series_delay_defaults_match_real_chat_pace():
    import random
    import statistics

    from AI.dialog.delivery import series_delay

    rng = random.Random(3)
    delays = [series_delay("x" * 25, None, rng) for _ in range(400)]

    # Real chats: median pause inside a series is ~12 s.
    assert 8 <= statistics.median(delays) <= 20
    assert min(delays) >= 3.0
    assert max(delays) <= 60.0


def test_history_measures_participant_series_pace():
    from AI.dialog.participant_imitation import ParticipantHistory

    entry = ParticipantHistory(chat_id="-1001", user_id=42, sample_size=50, recent_size=10)
    for turn in range(6):
        base = 1000 * turn
        entry.add_logged_message("начало", base)
        entry.add_logged_message("x" * 20, base + 10)  # 2 chars/s
        entry.add_logged_message("y" * 300, base + 11)  # paste: ignored

    timing = entry.style_stats()["series_timing"]

    assert timing["rates"] == [2.0] * 6
    assert timing["min_gap"] == 1.5


def test_regular_prompt_multiline_reply_stays_single_message():
    from AI.dialog.delivery import send_dialog_response

    message, _, replies = _message()

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("regular prompts must not be split")

    fake_bot = SimpleNamespace(send_chat_action=forbidden, send_message=forbidden)
    asyncio.run(
        send_dialog_response(
            message,
            "строка 1\nстрока 2",
            settings={"prompt_type": "standard"},
            bot=fake_bot,
            sleep=forbidden,
        )
    )

    assert replies == ["строка 1\nстрока 2"]


def test_series_delay_never_below_participant_minimum_gap():
    import random

    from AI.dialog.delivery import series_delay

    timing = {"rates": [10.0], "min_gap": 3.0}
    assert series_delay("ору", timing, random.Random(0)) == 3.0
