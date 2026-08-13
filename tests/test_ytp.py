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


class DummyMessage:
    def __init__(self):
        self.chat = SimpleNamespace(id=123)
        self.reply_to_message = None
        self.video = SimpleNamespace(file_id="video-file-id", file_size=1024, duration=5)
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
