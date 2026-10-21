import asyncio
from datetime import datetime
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


VALID_DREAM = (
    "Мне снилось, что я веду урок физкультуры у пингвинов, а они сдают норматив по прыжкам "
    "через мой собственный код. Директор школы — огромный голубь в галстуке, который нихуя "
    "не понимал в прыжках, но поставил мне двойку. Проснулся с чувством, что даже во сне меня "
    "отчитывает птица, блядь."
)

INTERPRETED_DREAM = (
    VALID_DREAM
    + "\n\n🔮 Сонник Упупы:\n"
    "▪️ Пингвины — к коллегам, которые ходят вразвалку.\n"
    "Итог: ты бот с комплексом физрука."
)


class AlwaysRng:
    @staticmethod
    def random():
        return 0.0


class NeverRng:
    @staticmethod
    def random():
        return 0.999


def _moscow(hour):
    from features.channel.mood import MOSCOW_TZ

    return MOSCOW_TZ.localize(datetime(2026, 10, 21, hour, 0))


def test_valid_dream_post_passes_validation():
    from features.channel.dreams import _validate_dream_post

    assert _validate_dream_post(VALID_DREAM, []) is None


def test_dream_post_requires_dream_story_and_profanity():
    from features.channel.dreams import _validate_dream_post

    not_a_dream = VALID_DREAM.replace("Мне снилось, что я веду", "Я веду").replace(
        "Проснулся с чувством, что даже во сне меня", "Ушёл с чувством, что даже на работе меня"
    )
    assert "снилось" in _validate_dream_post(not_a_dream, [])

    clean_mouth = VALID_DREAM.replace("нихуя", "ничего").replace(", блядь", "")
    assert "матер" in _validate_dream_post(clean_mouth, [])

    too_long = VALID_DREAM + " бля" * 200
    assert "длиннее" in _validate_dream_post(too_long, [])


def test_dream_post_rejects_any_interpretation():
    from features.channel.dreams import _validate_dream_post

    assert "толкование не нужно" in _validate_dream_post(INTERPRETED_DREAM, [])
    assert "толкование не нужно" in _validate_dream_post(VALID_DREAM + "\nИтог: я долбоёб.", [])
    assert "толкование не нужно" in _validate_dream_post(
        VALID_DREAM + " По соннику Миллера это к деньгам.", []
    )


def test_dream_post_rejects_repeating_previous_dream_plot():
    from features.channel.dreams import _validate_dream_post

    previous = {"post_kind": "dream", "text": VALID_DREAM}
    retold = VALID_DREAM.replace("поставил мне двойку", "поставил мне тройку")

    assert "похож на прошлый сон" in _validate_dream_post(retold, [previous])


def test_dream_post_has_cooldown_and_prefers_sleepy_mornings():
    from features.channel import dreams

    neutral_day = dreams.dream_post_probability({"name": "neutral"}, now=_moscow(14))
    sleepy_morning = dreams.dream_post_probability({"name": "sleepy"}, now=_moscow(8))
    evening = dreams.dream_post_probability({"name": "neutral"}, now=_moscow(21))

    assert sleepy_morning > neutral_day > evening
    assert sleepy_morning < 0.25

    assert dreams.should_try_dream_post([], {"name": "neutral"}, rng=AlwaysRng()) is True
    assert dreams.should_try_dream_post([], {"name": "neutral"}, rng=NeverRng()) is False
    history = [{"post_kind": "normal"} for _ in range(dreams.DREAM_POST_COOLDOWN_POSTS - 1)]
    history.append({"post_kind": "dream"})
    assert dreams.should_try_dream_post(history, {"name": "neutral"}, rng=AlwaysRng()) is False


def test_dream_prompt_asks_for_mocking_story_without_interpretation():
    from features.channel.dreams import _build_prompt

    previous = {"post_kind": "dream", "text": VALID_DREAM}
    prompt = _build_prompt({"name": "sleepy"}, [previous], retry_note="толкование не нужно")

    assert "мне снилось" in prompt
    assert "саркастично, иронично и издевательски" in prompt
    assert "мат" in prompt
    assert "НЕ толкуй сон" in prompt
    assert "Прошлые сны" in prompt
    assert "урок физкультуры у пингвинов" in prompt
    assert "Предыдущая попытка не прошла проверку: толкование не нужно" in prompt


def test_prepare_dream_post_retries_invalid_generation_and_marks_kind(monkeypatch):
    import AI.summarize as summarize
    from features.channel import dreams

    answers = iter([INTERPRETED_DREAM, "**" + VALID_DREAM + "**"])
    prompts = []

    async def fake_generate(prompt, chat_id):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(summarize, "_generate_with_active_model", fake_generate)

    text, metadata = asyncio.run(
        dreams.prepare_dream_post([], {"name": "sleepy", "posts_left": 2}, rng=AlwaysRng())
    )

    assert text == VALID_DREAM
    assert metadata["post_kind"] == "dream"
    assert metadata["mood"] == "sleepy"
    assert metadata["chat_context_used"] is False
    assert len(prompts) == 2
    assert "Предыдущая попытка не прошла проверку: толкование не нужно" in prompts[1]


def test_prepare_dream_post_gives_up_after_bad_attempts(monkeypatch):
    import AI.summarize as summarize
    from features.channel import dreams

    async def fake_generate(prompt, chat_id):
        return "мне похуй"

    monkeypatch.setattr(summarize, "_generate_with_active_model", fake_generate)

    assert asyncio.run(dreams.prepare_dream_post([], {"name": "neutral"}, rng=AlwaysRng())) is None


def test_channel_dream_post_does_not_trigger_chat_dream_book():
    from AI.dream_book import is_dream_teaser

    # Сам пост длинный и содержательный: если его перешлют в чат, он не станет «анонсом».
    assert not is_dream_teaser(VALID_DREAM)


def test_mood_service_can_publish_dream_post(monkeypatch):
    from features.channel import mood_service

    monkeypatch.setattr(mood_service.base, "load_posts", lambda: [])
    monkeypatch.setattr(mood_service, "get_current_mood", lambda: {"name": "sleepy", "posts_left": 3})
    monkeypatch.setattr(mood_service.chat_context, "should_force_chat_post", lambda _posts: False)

    async def nothing(*_args, **_kwargs):
        return None

    async def fake_dream(_posts, _mood):
        return VALID_DREAM, {"post_kind": "dream"}

    async def forbidden_voice(*_args, **_kwargs):
        raise AssertionError("voice should not be tried after a dream was selected")

    stored = []

    async def fake_store(sent, **kwargs):
        stored.append((sent, kwargs))

    async def fake_consume(_mood, _message_id):
        return None

    monkeypatch.setattr(mood_service.polls, "prepare_poll", nothing)
    monkeypatch.setattr(mood_service.verses, "prepare_bawdy_verse", nothing)
    monkeypatch.setattr(mood_service.dreams, "prepare_dream_post", fake_dream)
    monkeypatch.setattr(mood_service.voice_posts, "prepare_voice_post", forbidden_voice)
    monkeypatch.setattr(mood_service.base, "_store_published_post", fake_store)
    monkeypatch.setattr(mood_service, "_consume_after_publish", fake_consume)

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            assert chat_id == mood_service.CHANNEL_TARGET
            assert "parse_mode" not in kwargs
            return SimpleNamespace(message_id=504, text=text)

    sent, text = asyncio.run(mood_service.publish_channel_post(FakeBot(), source="scheduled"))

    assert sent.message_id == 504
    assert text == VALID_DREAM
    assert stored[0][1]["metadata"]["post_kind"] == "dream"


def test_failed_dream_mode_falls_back_to_other_formats(monkeypatch):
    from features.channel import mood_service

    monkeypatch.setattr(mood_service.base, "load_posts", lambda: [])
    monkeypatch.setattr(mood_service, "get_current_mood", lambda: {"name": "sleepy", "posts_left": 3})
    monkeypatch.setattr(mood_service.chat_context, "should_force_chat_post", lambda _posts: False)

    async def nothing(*_args, **_kwargs):
        return None

    async def broken_dream(_posts, _mood):
        raise RuntimeError("model is down")

    async def fake_store(*_args, **_kwargs):
        return None

    async def fake_consume(_mood, _message_id):
        return None

    monkeypatch.setattr(mood_service.polls, "prepare_poll", nothing)
    monkeypatch.setattr(mood_service.verses, "prepare_bawdy_verse", nothing)
    monkeypatch.setattr(mood_service.dreams, "prepare_dream_post", broken_dream)
    monkeypatch.setattr(mood_service.voice_posts, "prepare_voice_post", nothing)
    monkeypatch.setattr(
        mood_service.screams,
        "prepare_scream",
        lambda _posts, _mood: ("ЫАУАЫАААА", {"post_kind": "scream"}),
    )
    monkeypatch.setattr(mood_service.base, "_store_published_post", fake_store)
    monkeypatch.setattr(mood_service, "_consume_after_publish", fake_consume)

    class FakeBot:
        async def send_message(self, chat_id, text, **kwargs):
            return SimpleNamespace(message_id=505, text=text)

    sent, text = asyncio.run(mood_service.publish_channel_post(FakeBot(), source="scheduled"))

    assert text == "ЫАУАЫАААА"
