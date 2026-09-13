import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from aiogram.types import BufferedInputFile

# Настраивает fake env и тяжёлые моки до импорта прикладных модулей.
from tests import test_smoke_imports  # noqa: F401

import AI.adddescribe as adddescribe
import features.channels_settings as channels_settings
from infrastructure.media_io import (
    MediaDownloadTooLarge,
    download_telegram_bytes,
    download_url_bytes,
)


class FakeTelegramBot:
    def __init__(self, data=b"telegram-image", declared_size=None):
        self.data = data
        self.declared_size = declared_size
        self.download_called = False

    async def get_file(self, file_id):
        return SimpleNamespace(
            file_path=f"photos/{file_id}.jpg",
            file_size=self.declared_size,
        )

    async def download_file(self, file_path, destination):
        self.download_called = True
        destination.write(self.data)
        return destination


class RecordingMessage:
    def __init__(self):
        self.photo = None
        self.video = None
        self.caption = None

    async def answer_photo(self, media, caption=None):
        await asyncio.sleep(0)
        self.photo = media
        self.caption = caption

    async def answer_video(self, media, caption=None):
        await asyncio.sleep(0)
        self.video = media
        self.caption = caption


def test_adddescribe_uses_bot_downloader_without_tokenized_url(caplog):
    bot = FakeTelegramBot(data=b"safe-image")
    caplog.set_level(logging.INFO)

    result = asyncio.run(adddescribe.download_image(bot, "photo-1"))

    assert result == b"safe-image"
    assert bot.download_called is True
    assert "AAFakeTokenForSmokeTestsOnly" not in caplog.text

    source = Path(adddescribe.__file__).read_text(encoding="utf-8")
    assert "api.telegram.org/file/bot" not in source
    assert "requests.get" not in source
    assert "API_TOKEN" not in source


def test_telegram_download_rejects_declared_oversize_before_download():
    bot = FakeTelegramBot(data=b"unused", declared_size=11)

    with pytest.raises(MediaDownloadTooLarge):
        asyncio.run(download_telegram_bytes(bot, "large", max_bytes=10))

    assert bot.download_called is False


def test_url_download_enforces_streamed_size_limit():
    async def scenario():
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"123456")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await download_url_bytes(
                "https://example.com/media.jpg",
                client=client,
                max_bytes=5,
            )

    with pytest.raises(MediaDownloadTooLarge):
        asyncio.run(scenario())


def test_parallel_channel_sends_keep_own_media_in_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def fake_download(url, **_kwargs):
        await asyncio.sleep(0)
        return f"bytes:{url}".encode()

    monkeypatch.setattr(channels_settings, "download_url_bytes", fake_download)

    first = RecordingMessage()
    second = RecordingMessage()

    async def scenario():
        await asyncio.gather(
            channels_settings._send_media_file(
                first,
                "https://example.com/A.jpg",
                "image",
                caption="A",
            ),
            channels_settings._send_media_file(
                second,
                "https://example.com/B.jpg",
                "image",
                caption="B",
            ),
        )

    asyncio.run(scenario())

    assert isinstance(first.photo, BufferedInputFile)
    assert isinstance(second.photo, BufferedInputFile)
    assert first.photo.data == b"bytes:https://example.com/A.jpg"
    assert second.photo.data == b"bytes:https://example.com/B.jpg"
    assert first.caption == "A"
    assert second.caption == "B"
    assert list(tmp_path.iterdir()) == []


def test_channel_sender_has_no_blocking_requests_or_shared_temp_file():
    source = Path(channels_settings.__file__).read_text(encoding="utf-8")

    assert "import requests" not in source
    assert "requests.get" not in source
    assert "temp_media.jpg" not in source
    assert "temp_media.mp4" not in source
