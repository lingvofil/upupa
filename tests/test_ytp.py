"""Tests for YTP timeout handling."""
import asyncio
import sys
from types import SimpleNamespace

from tests import test_smoke_imports

_ = test_smoke_imports  # env + heavy dependency mocks

from services import ytp


class DummyProcessingMessage:
    def __init__(self):
        self.deleted = False

    async def delete(self):
        self.deleted = True


DEFAULT_VIDEO = object()


class DummyMessage:
    def __init__(self, *, video=DEFAULT_VIDEO, video_note=None, reply_to_message=None):
        self.chat = SimpleNamespace(id=123)
        self.reply_to_message = reply_to_message
        self.video = (
            SimpleNamespace(file_id="video-file-id", file_size=1024, duration=5)
            if video is DEFAULT_VIDEO
            else video
        )
        self.video_note = video_note
        self.animation = None
        self.audio = None
        self.voice = None
        self.document = None
        self.sticker = None
        self.replies = []
        self.processing_messages = []

    async def reply(self, text):
        self.replies.append(text)
        msg = DummyProcessingMessage()
        self.processing_messages.append(msg)
        return msg

    async def reply_video(self, video, **kwargs):
        self.replies.append(("video", video, kwargs))


class DummyBot:
    async def get_file(self, file_id):
        return SimpleNamespace(file_path=f"telegram/{file_id}.mp4")

    async def download_file(self, file_path, destination):
        with open(destination, "wb") as file:
            file.write(b"fake video")


def test_run_command_times_out():
    async def run():
        ok, output = await ytp.run_command(
            [sys.executable, "-c", "import time; time.sleep(1)"],
            timeout=0.05,
        )
        assert not ok
        assert "timed out" in output

    asyncio.run(run())


def test_ytp_timeout_releases_semaphore(monkeypatch):
    async def timeout_render(*args, **kwargs):
        raise asyncio.TimeoutError

    async def run():
        monkeypatch.setattr(ytp, "_ytp_semaphore", asyncio.Semaphore(1))
        monkeypatch.setattr(ytp, "_run_blocking_ytp", timeout_render)

        message = DummyMessage()
        await ytp.handle_ytp_command(message, DummyBot())

        assert not ytp._ytp_semaphore.locked()
        assert message.processing_messages[0].deleted
        assert any("Пупизация зависла" in reply for reply in message.replies)

    asyncio.run(run())


def test_ytp_accepts_video_note_reply(monkeypatch):
    async def fake_render(_func_name, _input_path, output_path, *_args, **_kwargs):
        with open(output_path, "wb") as file:
            file.write(b"fake ytp")

    async def fake_convert(_input_webm, output_mp4):
        with open(output_mp4, "wb") as file:
            file.write(b"fake mp4")
        return True

    async def run():
        monkeypatch.setattr(ytp, "_ytp_semaphore", asyncio.Semaphore(1))
        monkeypatch.setattr(ytp, "_run_blocking_ytp", fake_render)
        monkeypatch.setattr(ytp, "convert_webm_to_mp4", fake_convert)

        video_note = SimpleNamespace(file_id="video-note-file-id", file_size=1024, duration=5)
        source = DummyMessage(video=None, video_note=video_note)
        message = DummyMessage(video=None, reply_to_message=source)

        await ytp.handle_ytp_command(message, DummyBot())

        assert any(reply[0] == "video" for reply in message.replies if isinstance(reply, tuple))
        assert message.processing_messages[0].deleted

    asyncio.run(run())
