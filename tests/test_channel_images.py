import asyncio
import sys
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_image_posts_are_rare_and_have_cooldown():
    from features.channel.service import (
        IMAGE_POST_COOLDOWN_POSTS,
        IMAGE_POST_PROBABILITY,
        _should_try_image_post,
    )

    class ZeroRng:
        @staticmethod
        def random():
            return 0.0

    assert IMAGE_POST_PROBABILITY == 0.15
    assert IMAGE_POST_COOLDOWN_POSTS == 3
    assert _should_try_image_post([{"post_kind": "normal"}] * 3, rng=ZeroRng()) is True

    recent = [
        {"post_kind": "normal"},
        {"post_kind": "image"},
        {"post_kind": "normal"},
    ]
    assert _should_try_image_post(recent, rng=ZeroRng()) is False


def test_image_plan_is_strict_and_caption_stays_short_and_absurd():
    from features.channel.service import _parse_image_plan, _validate_image_plan

    plan = _parse_image_plan(
        "КАРТИНКА: реалистичный червяк в деловом костюме сидит один в пустом офисе\n"
        "ПОДПИСЬ: я на работе"
    )
    assert plan is not None
    image_prompt, caption = plan
    assert caption == "я на работе"
    assert _validate_image_plan(image_prompt, caption, []) is None

    assert _parse_image_plan("просто какой-то текст") is None
    assert "многословная" in _validate_image_plan(
        image_prompt,
        "раз два три четыре пять шесть семь восемь девять",
        [],
    )
    assert "легенду про батю" in _validate_image_plan(
        "реалистичный батя сидит на стуле и смотрит в стену",
        "ну привет",
        [],
    )
    assert "ссылок" in _validate_image_plan(
        "реалистичный червяк смотрит на https://example.com",
        "я смотрю",
        [],
    )


def test_channel_image_prompt_demands_two_lines_and_no_text_inside_image():
    from prompts.channel import CHANNEL_IMAGE_POST_PROMPT

    prompt = CHANNEL_IMAGE_POST_PROMPT.casefold()
    assert "картинка:" in prompt
    assert "подпись:" in prompt
    assert "абсурд" in prompt
    assert "без текста внутри" in prompt
    assert "не упоминай батю" in prompt


def test_channel_image_adapter_reuses_pollinations_first(monkeypatch):
    import AI
    from features.channel.image_generation import generate_channel_image

    calls = []

    async def translate(prompt):
        calls.append(("translate", prompt))
        return "translated prompt"

    async def pollinations(prompt):
        calls.append(("pollinations", prompt))
        return b"png-bytes"

    fake = SimpleNamespace(
        translate_to_en=translate,
        pollinations_generate=pollinations,
        PIPELINE_ID=None,
    )
    monkeypatch.setitem(sys.modules, "AI.picgeneration", fake)
    monkeypatch.setattr(AI, "picgeneration", fake, raising=False)

    image, provider = asyncio.run(generate_channel_image("червяк в офисе"))

    assert image == b"png-bytes"
    assert provider == "pollinations"
    assert calls == [
        ("translate", "червяк в офисе"),
        ("pollinations", "translated prompt"),
    ]


def test_publish_image_uses_send_photo_and_stores_metadata(monkeypatch):
    from features.channel import service

    published_records = []

    class FakeBot:
        def __init__(self):
            self.photo_calls = []
            self.message_calls = []

        async def send_photo(self, chat_id, photo, caption=None):
            self.photo_calls.append((chat_id, photo, caption))
            return SimpleNamespace(message_id=777)

        async def send_message(self, chat_id, text):
            self.message_calls.append((chat_id, text))
            return SimpleNamespace(message_id=778)

    async def fake_image_post(_published_posts):
        return b"image-bytes", "мне нормально", {
            "post_kind": "image",
            "chat_context_used": False,
            "image_prompt": "реалистичный червяк сидит в картонной коробке",
            "image_provider": "pollinations",
        }

    monkeypatch.setattr(service, "load_posts", lambda: [])
    monkeypatch.setattr(service, "append_post", lambda record: published_records.append(record))
    monkeypatch.setattr(service, "_should_try_image_post", lambda _posts: True)
    monkeypatch.setattr(service, "_try_generate_image_post", fake_image_post)

    bot = FakeBot()
    sent, text = asyncio.run(service.publish_channel_post(bot, source="test"))

    assert sent.message_id == 777
    assert text == "мне нормально"
    assert len(bot.photo_calls) == 1
    assert bot.message_calls == []
    assert bot.photo_calls[0][0] == service.CHANNEL_TARGET
    assert bot.photo_calls[0][2] == "мне нормально"
    assert published_records[0]["post_kind"] == "image"
    assert published_records[0]["image_provider"] == "pollinations"
    assert published_records[0]["image_prompt"] == "реалистичный червяк сидит в картонной коробке"
