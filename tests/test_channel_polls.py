import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


class ZeroRng:
    @staticmethod
    def random():
        return 0.0

    @staticmethod
    def randint(a, b):
        return a


def test_poll_plan_parser_and_validation():
    from features.channel.polls import _parse_poll_plan, _validate_poll_plan

    plan = _parse_poll_plan(
        "ВОПРОС: Кем мне сегодня быть?\n"
        "ВАРИАНТ: червяком\n"
        "ВАРИАНТ: министром\n"
        "ВАРИАНТ: табуреткой"
    )

    assert plan == (
        "Кем мне сегодня быть?",
        ["червяком", "министром", "табуреткой"],
    )
    assert _validate_poll_plan(*plan) is None
    assert "повторяются" in _validate_poll_plan("Кто?", ["я", "Я"])


def test_poll_probability_respects_active_poll_and_post_cooldown():
    from features.channel.polls import _should_try_poll

    posts = [{"post_kind": "normal"} for _ in range(20)]
    assert _should_try_poll(posts, {"polls": [], "next_eligible_post_count": 20}, rng=ZeroRng()) is True
    assert _should_try_poll(posts, {"polls": [], "next_eligible_post_count": 21}, rng=ZeroRng()) is False
    assert _should_try_poll(
        posts,
        {"polls": [{"status": "active"}], "next_eligible_post_count": 0},
        rng=ZeroRng(),
    ) is False


def test_register_poll_persists_twelve_hour_close_and_cooldown(tmp_path, monkeypatch):
    from features.channel import polls

    monkeypatch.setattr(polls, "POLL_STATE_FILE", tmp_path / "polls.json")
    sent = SimpleNamespace(
        message_id=77,
        poll=SimpleNamespace(id="poll-123"),
    )

    asyncio.run(
        polls.register_published_poll(
            sent,
            plan={"question": "Кто я?", "options": ["червяк", "шкаф"]},
            source="scheduled",
            published_count_before=10,
            rng=ZeroRng(),
        )
    )

    state = polls._read_state()
    record = state["polls"][0]
    created = polls._parse_dt(record["created_at"])
    closes = polls._parse_dt(record["closes_at"])

    assert record["status"] == "active"
    assert record["poll_id"] == "poll-123"
    assert record["message_id"] == 77
    assert closes - created == timedelta(hours=12)
    assert state["next_eligible_post_count"] == 10 + 1 + polls.POLL_COOLDOWN_MIN_POSTS


def test_due_poll_is_closed_then_reflected_and_saved_to_channel_history(tmp_path, monkeypatch):
    from features.channel import polls

    monkeypatch.setattr(polls, "POLL_STATE_FILE", tmp_path / "polls.json")
    saved_posts = []
    monkeypatch.setattr(polls, "append_post", saved_posts.append)

    async def fake_reflection(record):
        return "71% выбрали табуретку. Начинаю деревянеть."

    monkeypatch.setattr(polls, "_generate_reflection", fake_reflection)

    now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    polls._write_state({
        "next_eligible_post_count": 100,
        "polls": [
            {
                "status": "active",
                "poll_id": "p1",
                "message_id": 55,
                "question": "Кем мне быть?",
                "options": ["табуреткой", "министром"],
                "created_at": (now - timedelta(hours=13)).isoformat(),
                "closes_at": (now - timedelta(minutes=1)).isoformat(),
            }
        ],
    })

    final_poll = SimpleNamespace(
        total_voter_count=7,
        options=[
            SimpleNamespace(text="табуреткой", voter_count=5),
            SimpleNamespace(text="министром", voter_count=2),
        ],
    )

    class FakeBot:
        def __init__(self):
            self.closed = []
            self.messages = []

        async def stop_poll(self, chat_id, message_id):
            self.closed.append((chat_id, message_id))
            return final_poll

        async def send_message(self, chat_id, text):
            self.messages.append((chat_id, text))
            return SimpleNamespace(message_id=99)

    bot = FakeBot()
    asyncio.run(
        polls.process_due_polls(
            bot,
            channel_target="@upupa_channel",
            rng=ZeroRng(),
            now=now,
        )
    )

    state = polls._read_state()
    record = state["polls"][0]
    assert bot.closed == [("@upupa_channel", 55)]
    assert record["status"] == "awaiting_reflection"
    assert record["total_voter_count"] == 7
    assert record["results"][0] == {"text": "табуреткой", "voter_count": 5}

    reflection_time = polls._parse_dt(record["reflection_due_at"]) + timedelta(seconds=1)
    asyncio.run(
        polls.process_due_polls(
            bot,
            channel_target="@upupa_channel",
            rng=ZeroRng(),
            now=reflection_time,
        )
    )

    state = polls._read_state()
    record = state["polls"][0]
    assert record["status"] == "reflected"
    assert bot.messages == [
        ("@upupa_channel", "71% выбрали табуретку. Начинаю деревянеть.")
    ]
    assert saved_posts[0]["post_kind"] == "poll_reflection"
    assert saved_posts[0]["poll_total_voter_count"] == 7
    assert saved_posts[0]["poll_results"][0]["voter_count"] == 5


def test_mood_service_can_replace_a_regular_slot_with_an_anonymous_poll(monkeypatch):
    from features.channel import mood_service

    monkeypatch.setattr(mood_service.base, "load_posts", lambda: [])
    monkeypatch.setattr(mood_service, "get_current_mood", lambda: {"name": "normal", "posts_left": 5})

    async def fake_prepare(published_posts, mood):
        return {"question": "Кем быть?", "options": ["червяком", "шкафом"]}

    registered = []

    async def fake_register(sent, **kwargs):
        registered.append((sent, kwargs))

    stored = []

    async def fake_store(sent, **kwargs):
        stored.append((sent, kwargs))

    async def fake_consume(mood, message_id):
        return None

    monkeypatch.setattr(mood_service.polls, "prepare_poll", fake_prepare)
    monkeypatch.setattr(mood_service.polls, "register_published_poll", fake_register)
    monkeypatch.setattr(mood_service.base, "_store_published_post", fake_store)
    monkeypatch.setattr(mood_service, "_consume_after_publish", fake_consume)

    class FakeBot:
        def __init__(self):
            self.poll_calls = []

        async def send_poll(self, chat_id, **kwargs):
            self.poll_calls.append((chat_id, kwargs))
            return SimpleNamespace(message_id=321, poll=SimpleNamespace(id="p321"))

    bot = FakeBot()
    sent, text = asyncio.run(mood_service.publish_channel_post(bot, source="scheduled"))

    assert sent.message_id == 321
    assert text == "Кем быть?"
    assert bot.poll_calls[0][1]["is_anonymous"] is True
    assert bot.poll_calls[0][1]["allows_multiple_answers"] is False
    assert stored[0][1]["metadata"]["post_kind"] == "poll"
    assert registered[0][1]["published_count_before"] == 0
