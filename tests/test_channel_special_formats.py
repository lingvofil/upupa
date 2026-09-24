import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


class AlwaysRng:
    @staticmethod
    def random():
        return 0.0

    @staticmethod
    def choice(values):
        return values[0]


def test_bawdy_verse_requires_four_lines_and_profanity():
    from features.channel.verses import _validate_verse

    valid = "вышел ночью за вином\nперепутал двор с вокзалом\nдумал будет всё путём\nа получилось блядь с финалом"
    assert _validate_verse(valid, []) is None
    assert "четыре" in _validate_verse("раз\nдва\nтри", [])
    assert "матер" in _validate_verse("раз два\nтри четыре\nпять шесть\nсемь восемь", [])


def test_bawdy_verse_and_voice_have_long_cooldowns():
    from features.channel import verses, voice_posts

    assert verses.should_try_verse([], rng=AlwaysRng()) is True
    assert voice_posts.should_try_voice([], rng=AlwaysRng()) is True

    verse_history = [{"post_kind": "normal"} for _ in range(verses.VERSE_COOLDOWN_POSTS - 1)]
    verse_history.append({"post_kind": "bawdy_verse"})
    assert verses.should_try_verse(verse_history, rng=AlwaysRng()) is False

    voice_history = [{"post_kind": "normal"} for _ in range(voice_posts.VOICE_COOLDOWN_POSTS - 1)]
    voice_history.append({"post_kind": "voice"})
    assert voice_posts.should_try_voice(voice_history, rng=AlwaysRng()) is False


def test_voice_text_is_short_spoken_prose():
    from features.channel.voice_posts import _validate_voice_text

    assert _validate_voice_text("Короче, я сегодня решил ничего не объяснять. И правильно сделал.", []) is None
    assert "слов" in _validate_voice_text("мда", [])
    assert "разметку" in _validate_voice_text("Список:\n- один\n- два\n- три", [])


def test_mood_service_can_publish_bawdy_verse(monkeypatch):
    from features.channel import mood_service

    monkeypatch.setattr(mood_service.base, "load_posts", lambda: [])
    monkeypatch.setattr(mood_service, "get_current_mood", lambda: {"name": "normal", "posts_left": 5})
    monkeypatch.setattr(mood_service.chat_context, "should_force_chat_post", lambda _posts: False)

    async def no_poll(*_args, **_kwargs):
        return None

    async def fake_verse(*_args, **_kwargs):
        return "раз\nдва\nтри\nблядь четыре", {"post_kind": "bawdy_verse"}

    async def fail_voice(*_args, **_kwargs):
        raise AssertionError("voice should not be tried after a verse was selected")

    stored = []

    async def fake_store(sent, **kwargs):
        stored.append((sent, kwargs))

    async def fake_consume(_mood, _message_id):
        return None

    monkeypatch.setattr(mood_service.polls, "prepare_poll", no_poll)
    monkeypatch.setattr(mood_service.verses, "prepare_bawdy_verse", fake_verse)
    monkeypatch.setattr(mood_service.voice_posts, "prepare_voice_post", fail_voice)
    monkeypatch.setattr(mood_service.base, "_store_published_post", fake_store)
    monkeypatch.setattr(mood_service, "_consume_after_publish", fake_consume)

    class FakeBot:
        async def send_message(self, chat_id, text):
            assert chat_id == mood_service.CHANNEL_TARGET
            return SimpleNamespace(message_id=501, text=text)

    sent, text = asyncio.run(mood_service.publish_channel_post(FakeBot(), source="scheduled"))
    assert sent.message_id == 501
    assert "блядь" in text
    assert stored[0][1]["metadata"]["post_kind"] == "bawdy_verse"


def test_mood_service_can_publish_voice_note(monkeypatch):
    from features.channel import mood_service

    monkeypatch.setattr(mood_service.base, "load_posts", lambda: [])
    monkeypatch.setattr(mood_service, "get_current_mood", lambda: {"name": "normal", "posts_left": 5})
    monkeypatch.setattr(mood_service.chat_context, "should_force_chat_post", lambda _posts: False)

    async def no_poll(*_args, **_kwargs):
        return None

    async def no_verse(*_args, **_kwargs):
        return None

    async def fake_voice(*_args, **_kwargs):
        return b"mp3", "Я сейчас скажу одну хуйню и уйду.", {
            "post_kind": "voice",
            "voice_provider": "gemini",
        }

    stored = []
    voice_calls = []

    async def fake_store(sent, **kwargs):
        stored.append((sent, kwargs))

    async def fake_consume(_mood, _message_id):
        return None

    monkeypatch.setattr(mood_service.polls, "prepare_poll", no_poll)
    monkeypatch.setattr(mood_service.verses, "prepare_bawdy_verse", no_verse)
    monkeypatch.setattr(mood_service.voice_posts, "prepare_voice_post", fake_voice)
    monkeypatch.setattr(mood_service.base, "_store_published_post", fake_store)
    monkeypatch.setattr(mood_service, "_consume_after_publish", fake_consume)

    class FakeBot:
        async def send_voice(self, chat_id, voice):
            voice_calls.append((chat_id, voice))
            return SimpleNamespace(message_id=502)

    sent, text = asyncio.run(mood_service.publish_channel_post(FakeBot(), source="scheduled"))
    assert sent.message_id == 502
    assert text == "Я сейчас скажу одну хуйню и уйду."
    assert voice_calls[0][0] == mood_service.CHANNEL_TARGET
    assert stored[0][1]["metadata"]["post_kind"] == "voice"
