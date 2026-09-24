import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.media_change as media_change


def _message(*, video_note=None, reply_to_message=None):
    return SimpleNamespace(
        reply_to_message=reply_to_message,
        video=None,
        video_note=video_note,
        animation=None,
        audio=None,
        voice=None,
        document=None,
        sticker=None,
    )


def _video_note():
    return SimpleNamespace(
        file_id="video-note-file",
        file_size=1024,
        duration=12,
    )


async def _async_noop(*args, **kwargs):
    return None


@pytest.mark.parametrize(
    "extractor",
    [media_change._extract_media_source, media_change._extract_reversible_media_source],
)
def test_video_note_reply_is_supported_by_speed_and_reverse_extractors(extractor):
    source = _message(video_note=_video_note())
    command = _message(reply_to_message=source)

    assert extractor(command) is source
    assert media_change._get_duration_seconds(source) == 12


def test_speed_video_note_applies_shared_branding_before_speed_change(monkeypatch):
    source = _message(video_note=_video_note())
    command = _message(reply_to_message=source)
    processing_message = SimpleNamespace(delete=_async_noop)
    replies = []
    video_replies = []
    branding_calls = []
    speed_calls = []

    async def reply(text):
        replies.append(text)
        return processing_message

    async def reply_video(media):
        video_replies.append(media)

    command.reply = reply
    command.reply_video = reply_video
    command.reply_voice = _async_noop
    command.reply_audio = _async_noop

    class FakeBot:
        async def get_file(self, file_id):
            assert file_id == "video-note-file"
            return SimpleNamespace(file_path="video_notes/input.mp4")

        async def download_file(self, file_path, destination):
            assert file_path == "video_notes/input.mp4"
            Path(destination).write_bytes(b"video-note")

    async def fake_brand(input_path, output_path):
        branding_calls.append((input_path, output_path))
        Path(output_path).write_bytes(b"branded")
        return output_path

    async def fake_speed(input_path, output_path, speed, with_audio):
        speed_calls.append((input_path, output_path, speed, with_audio))
        Path(output_path).write_bytes(b"changed")
        return True, ""

    monkeypatch.setattr(media_change, "prepare_video_note_for_processing", fake_brand)
    monkeypatch.setattr(media_change, "_change_speed_ffmpeg", fake_speed)

    asyncio.run(media_change.handle_fast_command(command, FakeBot()))

    assert replies == ["⚙️ меняю скорость..."]
    assert len(branding_calls) == 1
    assert len(speed_calls) == 1
    assert speed_calls[0][0] == branding_calls[0][1]
    assert speed_calls[0][2:] == (2.0, True)
    assert len(video_replies) == 1


def test_reverse_video_note_applies_shared_branding_before_reverse(monkeypatch):
    source = _message(video_note=_video_note())
    command = _message(reply_to_message=source)
    processing_message = SimpleNamespace(delete=_async_noop)
    replies = []
    video_replies = []
    branding_calls = []
    reverse_calls = []

    async def reply(text):
        replies.append(text)
        return processing_message

    async def reply_video(media):
        video_replies.append(media)

    command.reply = reply
    command.reply_video = reply_video
    command.reply_voice = _async_noop
    command.reply_audio = _async_noop

    class FakeBot:
        async def get_file(self, file_id):
            assert file_id == "video-note-file"
            return SimpleNamespace(file_path="video_notes/input.mp4")

        async def download_file(self, file_path, destination):
            assert file_path == "video_notes/input.mp4"
            Path(destination).write_bytes(b"video-note")

    async def fake_brand(input_path, output_path):
        branding_calls.append((input_path, output_path))
        Path(output_path).write_bytes(b"branded")
        return output_path

    async def fake_reverse(input_path, output_path, with_audio):
        reverse_calls.append((input_path, output_path, with_audio))
        Path(output_path).write_bytes(b"reversed")
        return True, ""

    monkeypatch.setattr(media_change, "prepare_video_note_for_processing", fake_brand)
    monkeypatch.setattr(media_change, "_reverse_video_ffmpeg", fake_reverse)

    asyncio.run(media_change.handle_reverse_command(command, FakeBot()))

    assert replies == ["⚙️ обращаю вспять..."]
    assert len(branding_calls) == 1
    assert len(reverse_calls) == 1
    assert reverse_calls[0][0] == branding_calls[0][1]
    assert reverse_calls[0][2] is True
    assert len(video_replies) == 1
