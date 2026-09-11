import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)


def test_clean_post_title_keeps_only_first_non_empty_line():
    from features.channels_settings import _clean_post_title

    text = "\n   ГНЕЗДАЛЬ ОГУЕЙНАЯ - 100 Г   \n\nЭто блюдо прекрасно подойдет..."

    assert _clean_post_title(text) == "ГНЕЗДАЛЬ ОГУЕЙНАЯ - 100 Г"
    assert _clean_post_title("\n \t") is None
    assert _clean_post_title(None) is None


def test_neurorecipes_command_enables_post_title_caption(monkeypatch):
    from features import channels_settings

    captured = {}

    async def fake_process(_message, prepared_info):
        captured.update(prepared_info)
        return True

    monkeypatch.setattr(channels_settings, "_process_random_media", fake_process)

    message = SimpleNamespace(text="нейрорецепты")
    settings = {
        "нейрорецепты": {
            "url": "https://tgstat.ru/channel/@neural_recipes",
            "reply_message": "Чего бы паесть необычнава",
            "error_message": "Нихуя",
        }
    }

    asyncio.run(channels_settings.process_channel_command(message, settings))

    assert captured["caption_from_post_title"] is True


def test_other_channel_command_does_not_enable_post_title_caption(monkeypatch):
    from features import channels_settings

    captured = {}

    async def fake_process(_message, prepared_info):
        captured.update(prepared_info)
        return True

    monkeypatch.setattr(channels_settings, "_process_random_media", fake_process)

    message = SimpleNamespace(text="рецепты")
    settings = {
        "рецепты": {
            "url": "https://tgstat.ru/channel/@uhbla",
            "reply_message": "Чего бы паесть вкусненькова",
            "error_message": "Холодильник пуст",
        }
    }

    asyncio.run(channels_settings.process_channel_command(message, settings))

    assert captured["caption_from_post_title"] is False


def test_process_random_media_sends_first_post_line_as_caption(monkeypatch):
    from features import channels_settings

    sent = []

    class FakeMessage:
        def __init__(self):
            self.replies = []

        async def reply(self, text):
            self.replies.append(text)

    async def fake_download(_url):
        return {
            "url": "https://example.invalid/food.jpg",
            "type": "image",
            "post_text": "ГНЕЗДАЛЬ ОГУЕЙНАЯ - 100 Г\n\nПолное описание блюда",
        }

    async def fake_send(_message, url, media_type, caption=None):
        sent.append((url, media_type, caption))

    monkeypatch.setattr(channels_settings, "_download_random_media", fake_download)
    monkeypatch.setattr(channels_settings, "_send_media_file", fake_send)

    message = FakeMessage()
    result = asyncio.run(
        channels_settings._process_random_media(
            message,
            {
                "url": "https://tgstat.ru/channel/@neural_recipes",
                "reply_message": "Чего бы паесть необычнава",
                "error_message": "Нихуя",
                "caption_from_post_title": True,
            },
        )
    )

    assert result is True
    assert message.replies == ["Чего бы паесть необычнава"]
    assert sent == [
        (
            "https://example.invalid/food.jpg",
            "image",
            "ГНЕЗДАЛЬ ОГУЕЙНАЯ - 100 Г",
        )
    ]
