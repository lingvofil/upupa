import asyncio
from types import SimpleNamespace

from tests import test_smoke_imports

SMOKE_IMPORT_FIXTURES = test_smoke_imports


def test_safe_audio_filter_sanitizes_samples_before_codec():
    from services.distortion import get_safe_audio_distortion_filter

    audio_filter = get_safe_audio_distortion_filter(45)

    assert "acrusher=" in audio_filter
    assert "aresample=48000" in audio_filter
    assert "aformat=sample_fmts=s16" in audio_filter
    assert "alimiter=limit=0.95" in audio_filter


async def _failed_distorted_audio(*args, **kwargs):
    return False


async def _successful_original_audio(*args, **kwargs):
    return True


async def _unexpected_original_audio(*args, **kwargs):
    raise AssertionError("original audio fallback should not be used")


async def _successful_distorted_audio(*args, **kwargs):
    return True


def test_video_audio_falls_back_to_original_when_distortion_fails(monkeypatch):
    from services import distortion

    calls = []

    async def original_audio(input_path, output_path):
        calls.append((input_path, output_path))
        return await _successful_original_audio(input_path, output_path)

    monkeypatch.setattr(distortion, "_write_distorted_video_audio", _failed_distorted_audio)
    monkeypatch.setattr(distortion, "_write_original_video_audio", original_audio)

    ok = asyncio.run(distortion._prepare_video_audio_track("input.mp4", "audio.m4a", 45))

    assert ok is True
    assert calls == [("input.mp4", "audio.m4a")]


def test_video_audio_skips_fallback_when_distortion_succeeds(monkeypatch):
    from services import distortion

    monkeypatch.setattr(distortion, "_write_distorted_video_audio", _successful_distorted_audio)
    monkeypatch.setattr(distortion, "_write_original_video_audio", _unexpected_original_audio)

    ok = asyncio.run(distortion._prepare_video_audio_track("input.mp4", "audio.m4a", 45))

    assert ok is True


def test_distortion_command_accepts_video_note_reply():
    from services.distortion import is_distortion_command

    video_note = SimpleNamespace(file_id="video-note-file-id", file_size=1024, duration=5)
    source = SimpleNamespace(
        photo=None,
        sticker=None,
        audio=None,
        voice=None,
        text=None,
        video=None,
        video_note=video_note,
        animation=None,
        document=None,
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1),
        text="дисторшн",
        caption=None,
        reply_to_message=source,
    )

    assert is_distortion_command(message)
