"""Tests for YTP timeout handling, rendering, and input normalization."""
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


def test_should_normalize_video_by_size_or_duration():
    small = SimpleNamespace(file_size=2 * 1024 * 1024, duration=10)
    large = SimpleNamespace(file_size=13 * 1024 * 1024, duration=10)
    long = SimpleNamespace(file_size=2 * 1024 * 1024, duration=31)

    assert not ytp._should_normalize_video(small)
    assert ytp._should_normalize_video(large)
    assert ytp._should_normalize_video(long)


def test_normalize_video_uses_bounded_profile(monkeypatch):
    captured = {}

    async def fake_run_command(command, timeout):
        captured["command"] = command
        captured["timeout"] = timeout
        return True, ""

    async def run():
        monkeypatch.setattr(ytp, "run_command", fake_run_command)
        assert await ytp.normalize_video_for_ytp("input.mp4", "output.mp4")

        command = captured["command"]
        vf = command[command.index("-vf") + 1]
        assert "min(1280,iw)" in vf
        assert "min(1280,ih)" in vf
        assert "fps=30" in vf
        assert command[command.index("-c:v") + 1] == "libx264"
        assert captured["timeout"] == ytp.YTP_NORMALIZE_TIMEOUT_SEC

    asyncio.run(run())


def test_video_render_uses_h264_mp4_profile(monkeypatch):
    captured = {}

    class FakeSnippet:
        def __init__(self, duration):
            self.duration = duration

        def close(self):
            pass

    class FakeSource:
        duration = 5.0

        def subclip(self, start, end):
            return FakeSnippet(end - start)

        def close(self):
            pass

    class FakeFinal:
        def write_videofile(self, output_path, **kwargs):
            captured["output_path"] = output_path
            captured["kwargs"] = kwargs

        def close(self):
            pass

    monkeypatch.setattr(ytp, "VideoFileClip", lambda _path: FakeSource())
    monkeypatch.setattr(ytp, "concatenate_videoclips", lambda _clips: FakeFinal())
    monkeypatch.setattr(ytp.random, "uniform", lambda low, _high: low)
    monkeypatch.setattr(ytp.random, "choices", lambda *_args, **_kwargs: ["normal"])

    ytp._make_ytp_sync("input.mp4", "output.mp4", target_duration=0.3, preset="normal")

    kwargs = captured["kwargs"]
    assert captured["output_path"] == "output.mp4"
    assert kwargs["codec"] == "libx264"
    assert kwargs["audio_codec"] == "aac"
    assert kwargs["bitrate"] == "2500k"
    assert kwargs["audio_bitrate"] == "128k"
    assert kwargs["preset"] == "ultrafast"
    assert kwargs["temp_audiofile"].endswith(".m4a")
    assert kwargs["ffmpeg_params"] == ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]


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
    render_outputs = []

    async def fake_render(_func_name, _input_path, output_path, *_args, **_kwargs):
        render_outputs.append(output_path)
        with open(output_path, "wb") as file:
            file.write(b"fake mp4")

    async def run():
        monkeypatch.setattr(ytp, "_ytp_semaphore", asyncio.Semaphore(1))
        monkeypatch.setattr(ytp, "_run_blocking_ytp", fake_render)

        video_note = SimpleNamespace(file_id="video-note-file-id", file_size=1024, duration=5)
        source = DummyMessage(video=None, video_note=video_note)
        message = DummyMessage(video=None, reply_to_message=source)

        await ytp.handle_ytp_command(message, DummyBot())

        assert render_outputs and render_outputs[0].endswith(".mp4")
        assert any(reply[0] == "video" for reply in message.replies if isinstance(reply, tuple))
        assert message.processing_messages[0].deleted

    asyncio.run(run())


def test_long_video_is_normalized_before_render(monkeypatch):
    render_inputs = []
    render_outputs = []
    normalize_calls = []

    async def fake_normalize(input_path, output_path):
        normalize_calls.append((input_path, output_path))
        with open(output_path, "wb") as file:
            file.write(b"normalized video")
        return True

    async def fake_render(_func_name, input_path, output_path, *_args, **_kwargs):
        render_inputs.append(input_path)
        render_outputs.append(output_path)
        with open(output_path, "wb") as file:
            file.write(b"fake mp4")

    async def run():
        monkeypatch.setattr(ytp, "_ytp_semaphore", asyncio.Semaphore(1))
        monkeypatch.setattr(ytp, "normalize_video_for_ytp", fake_normalize)
        monkeypatch.setattr(ytp, "_run_blocking_ytp", fake_render)

        video = SimpleNamespace(
            file_id="long-video-file-id",
            file_size=5 * 1024 * 1024,
            duration=60,
        )
        message = DummyMessage(video=video)
        await ytp.handle_ytp_command(message, DummyBot())

        assert normalize_calls
        assert render_inputs
        assert render_inputs[0].endswith("_normalized.mp4")
        assert render_outputs and render_outputs[0].endswith(".mp4")
        assert any(reply[0] == "video" for reply in message.replies if isinstance(reply, tuple))
        assert message.processing_messages[0].deleted

    asyncio.run(run())
