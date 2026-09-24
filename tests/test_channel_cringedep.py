import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_pick_cringedep_image_skips_already_used_source():
    from features.channel.cringedep_service import _pick_unanswered_image_post

    posts = [
        {"url": "https://t.me/cringedep/10", "image_url": "https://img/10.jpg", "text": "old"},
        {"url": "https://t.me/cringedep/11", "image_url": "https://img/11.jpg", "text": "new"},
    ]
    published = [{"external_source_url": "https://t.me/cringedep/10"}]

    assert _pick_unanswered_image_post(posts, published)["url"] == "https://t.me/cringedep/11"


def test_cringedep_pun_is_grounded_and_text_is_overlaid(monkeypatch):
    from AI import summarize
    from features.channel import cringedep_service
    from features.channel import image_generation

    source_url = "https://t.me/cringedep/123"
    captured_prompts = []

    async def fake_fetch_public_posts(channel: str, *, limit: int):
        assert channel == "cringedep"
        assert limit == cringedep_service.CRINGEDEP_POSTS_LIMIT
        return [{"url": source_url, "image_url": "https://img/source.jpg", "text": ""}]

    async def fake_describe(_post):
        return "Рыжий кот и фото Эда Ширана. На изображении крупная подпись «CAT SHEERAN»."

    async def fake_generate(prompt: str, chat_id: str):
        captured_prompts.append(prompt)
        assert chat_id
        if "Ответь ровно одним словом: ДА или НЕТ" in prompt:
            assert "Кот Кобейн" in prompt
            assert "CAT SHEERAN" in prompt
            return "ДА"
        assert "лавровый лист" in prompt
        assert "ветку от исходной идеи" in prompt.casefold()
        return (
            "КАРТИНКА: рыжий кот с гранжевой гитарой и светлыми волосами, без надписей и текста\n"
            "ПОДПИСЬ: Кот Кобейн"
        )

    async def fake_image(prompt: str):
        assert "кот" in prompt.casefold()
        return b"generated-image", "gigachat"

    async def fake_overlay(image_bytes: bytes, text: str):
        assert image_bytes == b"generated-image"
        assert text == "Кот Кобейн"
        return b"overlaid-image"

    monkeypatch.setattr(cringedep_service.base, "fetch_public_posts", fake_fetch_public_posts)
    monkeypatch.setattr(cringedep_service.base, "_describe_external_image", fake_describe)
    monkeypatch.setattr(summarize, "_generate_with_active_model", fake_generate)
    monkeypatch.setattr(image_generation, "generate_channel_image", fake_image)
    monkeypatch.setattr(image_generation, "overlay_channel_text", fake_overlay)

    result = asyncio.run(
        cringedep_service._prepare_cringedep_pun([], {"name": "neutral", "posts_left": 4})
    )

    assert result is not None
    image_bytes, caption, metadata = result
    assert image_bytes == b"overlaid-image"
    assert caption == source_url
    assert metadata["post_kind"] == "image"
    assert metadata["image_subtype"] == "external_pun_reply"
    assert metadata["external_source_channel"] == "@cringedep"
    assert metadata["external_source_url"] == source_url
    assert metadata["external_pun_caption"] == "Кот Кобейн"
    assert metadata["image_provider"] == "gigachat"
    assert len(captured_prompts) == 2


def test_cringedep_rejects_copy_of_source_pun(monkeypatch):
    from AI import summarize
    from features.channel import cringedep_service
    from features.channel import image_generation

    async def fake_fetch_public_posts(_channel: str, *, limit: int):
        assert limit == cringedep_service.CRINGEDEP_POSTS_LIMIT
        return [{"url": "https://t.me/cringedep/124", "image_url": "https://img/source.jpg", "text": ""}]

    async def fake_describe(_post):
        return "Бутылка шампанского в космосе с подписью «парсекко»."

    async def fake_generate(_prompt: str, _chat_id: str):
        return "КАРТИНКА: бутылка шампанского летит между звёздами без текста\nПОДПИСЬ: парсекко"

    async def fail_image(_prompt: str):
        raise AssertionError("image generation must not run for copied source pun")

    monkeypatch.setattr(cringedep_service.base, "fetch_public_posts", fake_fetch_public_posts)
    monkeypatch.setattr(cringedep_service.base, "_describe_external_image", fake_describe)
    monkeypatch.setattr(summarize, "_generate_with_active_model", fake_generate)
    monkeypatch.setattr(image_generation, "generate_channel_image", fail_image)

    result = asyncio.run(
        cringedep_service._prepare_cringedep_pun([], {"name": "neutral", "posts_left": 4})
    )
    assert result is None


def test_cringedep_rejects_unrelated_pun_via_grounding_judge(monkeypatch):
    from AI import summarize
    from features.channel import cringedep_service
    from features.channel import image_generation

    async def fake_fetch_public_posts(_channel: str, *, limit: int):
        return [{"url": "https://t.me/cringedep/126", "image_url": "https://img/source.jpg", "text": ""}]

    async def fake_describe(_post):
        return "Рыжий кот рядом с Эдом Шираном и надписью CAT SHEERAN."

    async def fake_generate(prompt: str, _chat_id: str):
        if "Ответь ровно одним словом: ДА или НЕТ" in prompt:
            return "НЕТ"
        return "КАРТИНКА: лавровые листья и чёрный берет без текста\nПОДПИСЬ: Лавр Гвардейский"

    async def fail_image(_prompt: str):
        raise AssertionError("ungrounded pun must not reach image generation")

    monkeypatch.setattr(cringedep_service.base, "fetch_public_posts", fake_fetch_public_posts)
    monkeypatch.setattr(cringedep_service.base, "_describe_external_image", fake_describe)
    monkeypatch.setattr(summarize, "_generate_with_active_model", fake_generate)
    monkeypatch.setattr(image_generation, "generate_channel_image", fail_image)

    result = asyncio.run(
        cringedep_service._prepare_cringedep_pun([], {"name": "neutral", "posts_left": 4})
    )
    assert result is None


def test_publish_cringedep_pun_uses_overlay_image_and_only_link_as_caption(monkeypatch):
    from features.channel import cringedep_service

    stored = []
    consumed = []

    class FakeBot:
        def __init__(self):
            self.photo_calls = []

        async def send_photo(self, chat_id, photo, caption=None):
            self.photo_calls.append((chat_id, photo, caption))
            return SimpleNamespace(message_id=901)

    async def fake_prepare(_published, _mood):
        return b"image-bytes", "https://t.me/cringedep/125", {
            "post_kind": "image",
            "image_subtype": "external_pun_reply",
            "external_source_channel": "@cringedep",
            "external_source_url": "https://t.me/cringedep/125",
            "external_pun_caption": "Кот Кобейн",
            "image_provider": "gigachat",
        }

    async def fake_store(sent, *, source: str, text: str, metadata: dict):
        stored.append((sent.message_id, source, text, metadata))

    async def fake_consume(mood: dict, message_id: int | None):
        consumed.append((mood, message_id))

    async def fail_fallback(*_args, **_kwargs):
        raise AssertionError("normal publisher should not be called after successful cringedep reply")

    monkeypatch.setattr(cringedep_service.random, "random", lambda: 0.0)
    monkeypatch.setattr(cringedep_service.base, "load_posts", lambda: [])
    monkeypatch.setattr(cringedep_service.chat_context, "should_force_chat_post", lambda _posts: False)
    monkeypatch.setattr(cringedep_service, "get_current_mood", lambda: {"name": "neutral", "posts_left": 4})
    monkeypatch.setattr(cringedep_service, "_prepare_cringedep_pun", fake_prepare)
    monkeypatch.setattr(cringedep_service.base, "_store_published_post", fake_store)
    monkeypatch.setattr(cringedep_service.mood_service, "_consume_after_publish", fake_consume)
    monkeypatch.setattr(cringedep_service.mood_service, "publish_channel_post", fail_fallback)

    bot = FakeBot()
    sent, text = asyncio.run(cringedep_service.publish_channel_post(bot, source="test"))

    assert sent.message_id == 901
    assert text == "https://t.me/cringedep/125\n\nКот Кобейн"
    assert bot.photo_calls[0][0] == cringedep_service.CHANNEL_TARGET
    assert bot.photo_calls[0][2] == "https://t.me/cringedep/125"
    assert stored[0][1] == "test"
    assert stored[0][2] == text
    assert stored[0][3]["external_source_url"] == "https://t.me/cringedep/125"
    assert consumed == [({"name": "neutral", "posts_left": 4}, 901)]


def test_cringedep_mode_respects_existing_image_cooldown(monkeypatch):
    from features.channel import cringedep_service

    fallback_calls = []

    async def fake_fallback(_bot, *, source: str):
        fallback_calls.append(source)
        return SimpleNamespace(message_id=902), "обычный пост"

    monkeypatch.setattr(cringedep_service.random, "random", lambda: 0.0)
    monkeypatch.setattr(cringedep_service.base, "load_posts", lambda: [{"post_kind": "image"}])
    monkeypatch.setattr(cringedep_service.chat_context, "should_force_chat_post", lambda _posts: False)
    monkeypatch.setattr(cringedep_service.mood_service, "publish_channel_post", fake_fallback)

    sent, text = asyncio.run(cringedep_service.publish_channel_post(object(), source="scheduled"))

    assert sent.message_id == 902
    assert text == "обычный пост"
    assert fallback_calls == ["scheduled"]
