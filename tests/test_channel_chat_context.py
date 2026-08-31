import asyncio
import random
from datetime import datetime, timedelta
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def _message(dt, name, text):
    return {"dt": dt, "name": name, "text": text}


def test_chat_episode_uses_fresh_active_conversation_and_anonymizes_it():
    from features.channel.chat_context import pick_chat_episode

    now = datetime(2026, 8, 31, 12, 0)
    stale = [
        _message(now - timedelta(hours=30) + timedelta(minutes=i), "Старый", f"старое сообщение {i}")
        for i in range(6)
    ]
    fresh = [
        _message(now - timedelta(minutes=25), "Алиса", "кто опять заказал восемь одинаковых кружек?"),
        _message(now - timedelta(minutes=23), "Боб", "я не заказывал эти кружки"),
        _message(now - timedelta(minutes=21), "Алиса", "@detector говорит, что кружки сами пришли"),
        _message(now - timedelta(minutes=19), "Боб", "вот ссылка https://example.com/cups"),
        _message(now - timedelta(minutes=17), "Алиса", "теперь у нас восемь кружек и один стол"),
        _message(now - timedelta(minutes=15), "Боб", "предлагаю кружкам платить аренду"),
    ]

    def read_chat_log(chat_id: str):
        return stale if chat_id == "-1001" else fresh

    episode = pick_chat_episode(
        [],
        now=now,
        rng=random.Random(1),
        chats=[{"id": -1001}, {"id": -1002}],
        read_chat_log=read_chat_log,
    )

    assert episode is not None
    assert episode["message_count"] == 6
    assert episode["participant_count"] == 2
    assert episode["latest_at"] == (now - timedelta(minutes=15)).isoformat()
    assert "Алиса" not in episode["fragment"]
    assert "Боб" not in episode["fragment"]
    assert "@detector" not in episode["fragment"]
    assert "https://example.com/cups" not in episode["fragment"]
    assert "Участник 1" in episode["fragment"]
    assert "Участник 2" in episode["fragment"]
    assert len(episode["key"]) == 20


def test_recently_used_chat_episode_is_not_recycled():
    from features.channel.chat_context import pick_chat_episode

    now = datetime(2026, 8, 31, 12, 0)
    messages = [
        _message(now - timedelta(minutes=10 - i), "А", f"достаточно содержательная реплика номер {i}")
        if i % 2 == 0
        else _message(now - timedelta(minutes=10 - i), "Б", f"ещё одна содержательная реплика номер {i}")
        for i in range(6)
    ]

    first = pick_chat_episode(
        [],
        now=now,
        rng=random.Random(2),
        chats=[{"id": -1001}],
        read_chat_log=lambda _chat_id: messages,
    )
    assert first is not None

    second = pick_chat_episode(
        [{"created_at": now.isoformat(), "chat_context_key": first["key"], "chat_context_used": True}],
        now=now,
        rng=random.Random(2),
        chats=[{"id": -1001}],
        read_chat_log=lambda _chat_id: messages,
    )
    assert second is None


def test_daily_chat_guarantee_activates_after_three_recent_non_chat_posts():
    from features.channel.chat_context import should_force_chat_post

    now = datetime(2026, 8, 31, 12, 0)
    three_plain = [
        {"created_at": (now - timedelta(hours=hours)).isoformat(), "chat_context_used": False}
        for hours in (1, 2, 3)
    ]

    assert should_force_chat_post(three_plain[:2], now=now) is False
    assert should_force_chat_post(three_plain, now=now) is True
    assert should_force_chat_post(
        three_plain + [{"created_at": (now - timedelta(minutes=10)).isoformat(), "chat_context_used": True}],
        now=now,
    ) is False
    assert should_force_chat_post(
        [
            {"created_at": (now - timedelta(hours=30 + index)).isoformat(), "chat_context_used": False}
            for index in range(3)
        ],
        now=now,
    ) is False


def test_chat_prompt_cannot_treat_selected_context_as_optional():
    from features.channel.mood_service import _build_prompt, _grounded_chat_mode
    from prompts.channel import POST_LENGTH_MODES

    short = next(mode for mode in POST_LENGTH_MODES if mode["name"] == "short")
    prompt = _build_prompt(
        [],
        "Участник 1: опять обсуждают восемь кружек\nУчастник 2: кружки захватили стол",
        short,
        _grounded_chat_mode(),
        False,
        {"name": "neutral", "posts_left": 4},
    ).casefold()

    assert "обязательная опора для поста" in prompt
    assert "обязан использовать его как основу" in prompt
    assert "независимую фразу вместо реакции писать нельзя" in prompt
    assert "необязательный материал" not in prompt
    assert "можешь его проигнорировать" not in prompt


def test_daily_guarantee_bypasses_poll_and_image_modes(monkeypatch):
    from features.channel import mood_service

    recent_posts = [
        {"created_at": datetime.now().isoformat(), "chat_context_used": False}
        for _ in range(3)
    ]
    forced_episode = {
        "key": "episode-key",
        "fragment": "Участник 1: кружки наступают\nУчастник 2: защищаем стол",
        "latest_at": datetime.now().isoformat(),
        "message_count": 6,
        "participant_count": 2,
    }
    captured = {}

    monkeypatch.setattr(mood_service.base, "load_posts", lambda: recent_posts)
    monkeypatch.setattr(
        mood_service,
        "get_current_mood",
        lambda: {"name": "neutral", "posts_left": 4},
    )
    monkeypatch.setattr(mood_service.chat_context, "should_force_chat_post", lambda _posts: True)
    monkeypatch.setattr(mood_service.chat_context, "pick_chat_episode", lambda _posts: forced_episode)

    async def forbidden_poll(*_args, **_kwargs):
        raise AssertionError("poll mode must be skipped when chat guarantee is due")

    monkeypatch.setattr(mood_service.polls, "prepare_poll", forbidden_poll)

    def forbidden_image(*_args, **_kwargs):
        raise AssertionError("image mode must be skipped when chat guarantee is due")

    monkeypatch.setattr(mood_service, "_should_try_image_post", forbidden_image)

    async def fake_generate(mood, *, forced_chat_episode=None):
        captured["episode"] = forced_chat_episode
        return "кружки перешли в наступление", {
            "post_kind": "normal",
            "chat_context_used": True,
            "content_mode": "chat",
        }

    monkeypatch.setattr(mood_service, "generate_channel_post", fake_generate)

    async def fake_store(*_args, **_kwargs):
        return None

    async def fake_consume(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mood_service.base, "_store_published_post", fake_store)
    monkeypatch.setattr(mood_service, "_consume_after_publish", fake_consume)

    class FakeBot:
        async def send_message(self, _target, text):
            return SimpleNamespace(message_id=77, text=text)

    sent, text = asyncio.run(mood_service.publish_channel_post(FakeBot(), source="test"))

    assert sent.message_id == 77
    assert text == "кружки перешли в наступление"
    assert captured["episode"] == forced_episode
