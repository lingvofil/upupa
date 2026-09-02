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


class DummyResultQueue:
    def __init__(self, result=(True, None, None)):
        self.result = result
        self.closed = False

    def get(self, block, timeout):
        return self.result

    def close(self):
        self.closed = True


class DummyProcess:
    def __init__(self, *, alive=False, exitcode=0):
        self.alive = alive
        self.exitcode = exitcode
        self.pid = 4242
        self.started = False
        self.terminated = False
        self.killed = False
        self.closed = False
        self.join_calls = []

    def start(self):
        self.started = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def join(self, timeout=None):
        self.join_calls.append(timeout)

    def close(self):
        self.closed = True


class DummyContext:
    def __init__(self, process, result_queue):
        self.process = process
        self.result_queue = result_queue

    def Queue(self):
        return self.result_queue

    def Process(self, **kwargs):
        return self.process


def test_run_command_times_out():
    async def run():
        ok, output = await ytp.run_command(
            [sys.executable, "-c", "import time; time.sleep(1)"],
            timeout=0.05,
        )
        assert not ok
        assert "timed out" in output

    asyncio.run(run())


def test_ytp_worker_uses_spawn_and_nonblocking_join(monkeypatch):
    process = DummyProcess(alive=False)
    result_queue = DummyResultQueue()
    context = DummyContext(process, result_queue)
    requested_methods = []

    def get_context(method):
        requested_methods.append(method)
        return context

    monkeypatch.setattr(ytp.multiprocessing, "get_context", get_context)

    asyncio.run(ytp._run_blocking_ytp("_make_ytp_sync", "in", "out", timeout=0))

    assert requested_methods == ["spawn"]
    assert process.started
    assert process.join_calls == [0]
    assert process.closed
    assert result_queue.closed


def test_ytp_timeout_does_not_use_unbounded_join(monkeypatch):
    process = DummyProcess(alive=True)
    result_queue = DummyResultQueue()
    context = DummyContext(process, result_queue)

    monkeypatch.setattr(ytp.multiprocessing, "get_context", lambda method: context)
    monkeypatch.setattr(ytp, "YTP_TERMINATE_GRACE_SEC", 0)
    monkeypatch.setattr(ytp, "YTP_KILL_GRACE_SEC", 0)

    async def run():
        try:
            await ytp._run_blocking_ytp("_make_ytp_sync", "in", "out", timeout=0)
        except asyncio.TimeoutError:
            return
        raise AssertionError("YTP worker timeout was not raised")

    asyncio.run(run())

    assert process.terminated
    assert process.killed
    assert process.join_calls == []
    assert result_queue.closed


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
