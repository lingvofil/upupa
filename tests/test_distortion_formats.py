import asyncio
import os
from types import SimpleNamespace

from tests import test_smoke_imports

SMOKE_IMPORT_FIXTURES = test_smoke_imports


def _target(**overrides):
    values = {
        "photo": None,
        "sticker": None,
        "audio": None,
        "voice": None,
        "text": None,
        "video": None,
        "video_note": None,
        "animation": None,
        "document": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeMessage:
    def __init__(self, target):
        self.reply_to_message = target
        self.text = "дисторшн"
        self.caption = None
        self.chat = SimpleNamespace(id=123)
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)


def test_media_extension_prefers_original_filename():
    from services.distortion_formats import AUDIO_EXTENSIONS, media_extension

    media = SimpleNamespace(file_name="recording.M4A", mime_type="audio/mpeg")

    assert media_extension(media, ".mp3", allowed=AUDIO_EXTENSIONS) == ".m4a"


def test_media_extension_falls_back_to_mime_type():
    from services import distortion
    from services.distortion_formats import media_extension

    media = SimpleNamespace(file_name=None, mime_type="image/gif")

    assert media_extension(
        media,
        ".mp4",
        allowed=distortion.SUPPORTED_VIDEO_EXTENSIONS,
    ) == ".gif"


def test_audio_transcode_uses_codec_for_original_container(monkeypatch):
    from services import distortion
    from services.distortion_formats import transcode_audio_to_extension

    commands = []

    async def fake_run(command):
        commands.append(command)
        return True, "Success"

    monkeypatch.setattr(distortion, "run_ffmpeg_command", fake_run)

    assert asyncio.run(transcode_audio_to_extension("source.mp3", "result.ogg")) is True
    command = commands[0]
    assert "libopus" in command
    assert command[-1] == "result.ogg"


def test_video_transcode_uses_webm_codecs(monkeypatch):
    from services import distortion
    from services.distortion_formats import transcode_video_to_extension

    commands = []

    async def fake_has_audio(_path):
        return True

    async def fake_run(command):
        commands.append(command)
        return True, "Success"

    monkeypatch.setattr(distortion, "_has_audio_stream", fake_has_audio)
    monkeypatch.setattr(distortion, "run_ffmpeg_command", fake_run)

    assert asyncio.run(transcode_video_to_extension("source.mp4", "result.webm")) is True
    command = commands[0]
    assert "libvpx-vp9" in command
    assert "libopus" in command
    assert command[-1] == "result.webm"


def test_handler_preserves_video_document_extension(monkeypatch, tmp_path):
    from services import distortion
    from services.distortion_formats import handle_format_preserving_distortion_request

    captured = {}
    document = SimpleNamespace(
        file_id="video-file",
        file_name="camera.webm",
        mime_type="video/webm",
    )
    message = FakeMessage(_target(document=document))

    async def fake_download(_file_id, local_path):
        captured["download_path"] = local_path
        return True

    async def fake_worker(_token, _chat_id, media_info, _intensity):
        captured["media_info"] = dict(media_info)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(distortion, "download_file", fake_download)
    monkeypatch.setattr(distortion, "distortion_worker_async", fake_worker)
    monkeypatch.setattr(distortion, "main_bot_instance", SimpleNamespace(token="token"))
    monkeypatch.setattr("services.distortion_formats.random.randint", lambda *_args: 1234)

    asyncio.run(handle_format_preserving_distortion_request(message, distortion_module=distortion))

    assert captured["download_path"].endswith(os.path.join("temp_worker_1234", "input.webm"))
    assert captured["media_info"]["media_type"] == "video_document"
    assert captured["media_info"]["ext"] == ".webm"
    assert captured["media_info"]["output_file_name"] == "camera_distorted.webm"


def test_handler_preserves_audio_extension(monkeypatch, tmp_path):
    from services import distortion
    from services.distortion_formats import handle_format_preserving_distortion_request

    captured = {}
    audio = SimpleNamespace(
        file_id="audio-file",
        file_name="song.m4a",
        mime_type="audio/mp4",
    )
    message = FakeMessage(_target(audio=audio))

    async def fake_download(_file_id, local_path):
        captured["download_path"] = local_path
        return True

    async def fake_worker(_token, _chat_id, media_info, _intensity):
        captured["media_info"] = dict(media_info)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(distortion, "download_file", fake_download)
    monkeypatch.setattr(distortion, "distortion_worker_async", fake_worker)
    monkeypatch.setattr(distortion, "main_bot_instance", SimpleNamespace(token="token"))
    monkeypatch.setattr("services.distortion_formats.random.randint", lambda *_args: 5678)

    asyncio.run(handle_format_preserving_distortion_request(message, distortion_module=distortion))

    assert captured["download_path"].endswith(os.path.join("temp_worker_5678", "input.m4a"))
    assert captured["media_info"]["media_type"] == "audio"
    assert captured["media_info"]["ext"] == ".m4a"
    assert captured["media_info"]["output_file_name"] == "song_distorted.m4a"
