import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.media_change as media_change


def _message(*, sticker=None, reply_to_message=None):
    return SimpleNamespace(
        reply_to_message=reply_to_message,
        video=None,
        animation=None,
        audio=None,
        voice=None,
        document=None,
        sticker=sticker,
    )


def _video_sticker():
    return SimpleNamespace(
        file_id="video-sticker-file",
        file_size=1024,
        is_video=True,
        is_animated=False,
    )


@pytest.mark.parametrize(
    "extractor",
    [media_change._extract_media_source, media_change._extract_reversible_media_source],
)
def test_video_sticker_reply_is_supported_by_speed_and_reverse_extractors(extractor):
    source = _message(sticker=_video_sticker())
    command = _message(reply_to_message=source)

    assert extractor(command) is source


def test_reverse_extractor_does_not_expand_to_static_or_animated_stickers():
    for sticker in (
        SimpleNamespace(is_video=False, is_animated=False),
        SimpleNamespace(is_video=False, is_animated=True),
    ):
        source = _message(sticker=sticker)
        command = _message(reply_to_message=source)

        assert media_change._extract_reversible_media_source(command) is None


def test_reverse_video_sticker_uses_webm_input_and_returns_video(monkeypatch):
    source = _message(sticker=_video_sticker())
    processing_message = SimpleNamespace(delete=_async_noop)
    replies = []
    video_replies = []

    async def reply(text):
        replies.append(text)
        return processing_message

    async def reply_video(media):
        video_replies.append(media)

    command = _message(reply_to_message=source)
    command.reply = reply
    command.reply_video = reply_video
    command.reply_voice = _async_noop
    command.reply_audio = _async_noop

    class FakeBot:
        async def get_file(self, file_id):
            assert file_id == "video-sticker-file"
            return SimpleNamespace(file_path="stickers/video.webm")

        async def download_file(self, file_path, destination):
            assert file_path == "stickers/video.webm"
            Path(destination).write_bytes(b"fake-webm")

    calls = []

    async def fake_reverse(input_path, output_path, with_audio):
        calls.append((input_path, output_path, with_audio))
        assert input_path.endswith(".webm")
        Path(output_path).write_bytes(b"fake-mp4")
        return True, ""

    monkeypatch.setattr(media_change, "_reverse_video_ffmpeg", fake_reverse)

    asyncio.run(media_change.handle_reverse_command(command, FakeBot()))

    assert replies == ["⚙️ обращаю вспять..."]
    assert len(video_replies) == 1
    assert len(calls) == 1
    assert calls[0][2] is True


async def _async_noop(*args, **kwargs):
    return None
