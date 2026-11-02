import asyncio

import pytest

# Настраивает fake env и моки тяжёлых библиотек до импорта features.
from tests import test_smoke_imports

del test_smoke_imports

import features.channels_settings as channels_settings
from features.channels_settings import (
    _download_random_media,
    _extract_post_title,
    _telegram_media_from_html,
)


TELEGRAM_FEED = """
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="cats/1">
    <a class="tgme_widget_message_photo_wrap blured js-message_photo"
       style="width:225px;background-image:url('https://cdn.example/photo.jpg')"></a>
    <div class="tgme_widget_message_text js-message_text"><b>Жирный</b> заголовок<br/>Второй абзац</div>
  </div>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="cats/2">
    <video src="https://cdn.example/clip.mp4?token=abc" class="tgme_widget_message_video"></video>
  </div>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="cats/3">
    <video src="https://cdn.example/clip.mp4?token=abc"></video>
    <video class="without_src"></video>
    <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn.example/placeholder.jpg')"></a>
    <div class="tgme_widget_message_text">Просто текст</div>
  </div>
</div>
<div class="tgme_widget_message js-widget_message" data-post="cats/4">
  <div class="tgme_widget_message_text">Пост без медиа</div>
</div>
<img src="https://cdn.example/outside_of_post.jpg" />
"""


def test_feed_parser_finds_post_media_with_its_text():
    media = _telegram_media_from_html(TELEGRAM_FEED)

    assert [(item["type"], item["url"]) for item in media] == [
        ("image", "https://cdn.example/photo.jpg"),
        ("video", "https://cdn.example/clip.mp4?token=abc"),
    ]
    assert _extract_post_title(media[0]["post_text"]) == "Жирный заголовок"
    assert media[1]["post_text"] is None


def test_feed_parser_returns_nothing_for_page_without_posts():
    assert _telegram_media_from_html("<html><body>Канал закрыт</body></html>") == []


def test_channel_media_comes_from_feed_without_launching_chromium(monkeypatch):
    requested = []

    async def fake_download(url, **kwargs):
        requested.append((url, kwargs["max_bytes"]))
        return TELEGRAM_FEED.encode("utf-8")

    def forbidden_browser():
        raise AssertionError("Chromium не должен запускаться ради ленты t.me/s")

    monkeypatch.setattr(channels_settings, "download_url_bytes", fake_download)
    monkeypatch.setattr(channels_settings, "async_playwright", forbidden_browser)
    monkeypatch.setattr(channels_settings.random, "choice", lambda items: items[1])

    media = asyncio.run(_download_random_media("https://tgstat.ru/channel/@cats_cats"))

    assert media["url"] == "https://cdn.example/clip.mp4?token=abc"
    assert requested == [
        ("https://t.me/s/cats_cats", channels_settings._TELEGRAM_FEED_MAX_BYTES)
    ]


@pytest.mark.parametrize("feed", [None, b"<html>no posts</html>"])
def test_chromium_stays_as_fallback_when_feed_gives_nothing(monkeypatch, feed):
    class BrowserRequested(Exception):
        pass

    async def fake_download(_url, **_kwargs):
        if feed is None:
            raise OSError("t.me недоступен")
        return feed

    def browser():
        raise BrowserRequested()

    monkeypatch.setattr(channels_settings, "download_url_bytes", fake_download)
    monkeypatch.setattr(channels_settings, "async_playwright", browser)

    with pytest.raises(BrowserRequested):
        asyncio.run(_download_random_media("https://tgstat.ru/channel/@cats_cats"))


def test_source_without_telegram_feed_goes_straight_to_browser(monkeypatch):
    class BrowserRequested(Exception):
        pass

    async def forbidden_download(_url, **_kwargs):
        raise AssertionError("Лента Telegram для этого источника не нужна")

    def browser():
        raise BrowserRequested()

    monkeypatch.setattr(channels_settings, "download_url_bytes", forbidden_download)
    monkeypatch.setattr(channels_settings, "async_playwright", browser)

    with pytest.raises(BrowserRequested):
        asyncio.run(_download_random_media("https://example.com/feed"))
