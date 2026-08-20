import asyncio
import os
from types import SimpleNamespace

import numpy as np
from PIL import Image

from tests import test_smoke_imports

SMOKE_IMPORT_FIXTURES = test_smoke_imports


def test_static_webp_distortion_preserves_rgba(monkeypatch, tmp_path):
    from services import distortion_stickers as stickers

    class FakeSeamCarving:
        @staticmethod
        def resize(array, size, energy_mode=None, order=None):
            image = Image.fromarray(array)
            return np.array(image.resize(size, Image.BILINEAR))

    monkeypatch.setattr(stickers, "seam_carving", FakeSeamCarving)
    monkeypatch.setattr(stickers, "SEAM_CARVING_AVAILABLE", True)

    input_path = tmp_path / "input.webp"
    output_path = tmp_path / "output.webp"
    source = Image.new("RGBA", (96, 64), (255, 0, 0, 0))
    for x in range(24, 72):
        for y in range(12, 52):
            source.putpixel((x, y), (255, 50, 50, 220))
    source.save(input_path, "WEBP", lossless=True)

    ok = asyncio.run(
        stickers.apply_rgba_static_sticker_distortion(
            str(input_path), str(output_path), 45
        )
    )

    assert ok is True
    assert output_path.exists()
    assert output_path.stat().st_size <= stickers.TELEGRAM_STATIC_STICKER_MAX_BYTES
    with Image.open(output_path) as result:
        rgba = result.convert("RGBA")
        assert rgba.size == (512, 512)
        alpha_min, alpha_max = rgba.getchannel("A").getextrema()
        assert alpha_min < 255
        assert alpha_max > 0


def test_video_sticker_encoder_uses_vp9_no_audio_and_telegram_limits(monkeypatch, tmp_path):
    from services import distortion, distortion_stickers as stickers

    calls = []
    output_path = tmp_path / "output.webm"

    async def fake_ffmpeg(command):
        calls.append(command)
        output_path.write_bytes(b"webm")
        return True, "Success"

    async def fake_media_info(path):
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "vp9",
                    "width": 512,
                    "height": 512,
                    "avg_frame_rate": "12/1",
                    "duration": "2.90",
                }
            ],
            "format": {"duration": "2.90"},
        }

    monkeypatch.setattr(distortion, "run_ffmpeg_command", fake_ffmpeg)
    monkeypatch.setattr(distortion, "get_media_info", fake_media_info)

    ok = asyncio.run(
        stickers.encode_media_as_video_sticker("input.mp4", str(output_path))
    )

    assert ok is True
    assert len(calls) == 1
    command = calls[0]
    assert "libvpx-vp9" in command
    assert "-an" in command
    assert "-t" in command
    assert str(stickers.TELEGRAM_VIDEO_STICKER_MAX_SECONDS) in command
    assert "yuva420p" in command
    assert str(output_path).endswith(".webm")


def test_video_sticker_validation_rejects_audio(monkeypatch, tmp_path):
    from services import distortion, distortion_stickers as stickers

    output_path = tmp_path / "bad.webm"
    output_path.write_bytes(b"webm")

    async def fake_media_info(path):
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "vp9",
                    "width": 512,
                    "height": 512,
                    "avg_frame_rate": "12/1",
                },
                {"codec_type": "audio", "codec_name": "opus"},
            ],
            "format": {"duration": "2.5"},
        }

    monkeypatch.setattr(distortion, "get_media_info", fake_media_info)

    assert asyncio.run(stickers.validate_telegram_video_sticker(str(output_path))) is False


class _FakeSession:
    async def close(self):
        return None


class _FakeBot:
    def __init__(self):
        self.calls = []
        self.session = _FakeSession()

    async def send_sticker(self, chat_id, sticker):
        self.calls.append(("sticker", chat_id, str(sticker)))

    async def send_message(self, chat_id, text):
        self.calls.append(("message", chat_id, text))


def _fake_distortion_module(bot, *, convert_tgs=None):
    async def default_convert(input_path, output_path):
        with open(output_path, "wb") as file:
            file.write(b"converted")
        return True

    return SimpleNamespace(
        Bot=lambda token: bot,
        FSInputFile=lambda path: f"FILE:{path}",
        convert_tgs_to_webm=convert_tgs or default_convert,
    )


def test_static_sticker_worker_replies_with_send_sticker(monkeypatch, tmp_path):
    from services import distortion_stickers as stickers

    bot = _FakeBot()
    input_path = tmp_path / "input.webp"
    input_path.write_bytes(b"input")

    async def fake_static(input_file, output_file, intensity):
        with open(output_file, "wb") as file:
            file.write(b"output")
        return True

    monkeypatch.setattr(stickers, "apply_rgba_static_sticker_distortion", fake_static)

    asyncio.run(
        stickers.distortion_sticker_worker_async(
            "token",
            123,
            {"media_type": "sticker_static", "local_path": str(input_path)},
            45,
            distortion_module=_fake_distortion_module(bot),
        )
    )

    assert len(bot.calls) == 1
    assert bot.calls[0][0] == "sticker"
    assert "_out.webp" in bot.calls[0][2]


def test_video_sticker_worker_replies_with_webm_sticker(monkeypatch, tmp_path):
    from services import distortion_stickers as stickers

    bot = _FakeBot()
    input_path = tmp_path / "input.webm"
    input_path.write_bytes(b"input")

    async def fake_video(input_file, output_file, intensity):
        with open(output_file, "wb") as file:
            file.write(b"output")
        return True

    monkeypatch.setattr(stickers, "create_distorted_video_sticker", fake_video)

    asyncio.run(
        stickers.distortion_sticker_worker_async(
            "token",
            123,
            {"media_type": "sticker_video", "local_path": str(input_path)},
            45,
            distortion_module=_fake_distortion_module(bot),
        )
    )

    assert len(bot.calls) == 1
    assert bot.calls[0][0] == "sticker"
    assert "_out.webm" in bot.calls[0][2]


def test_tgs_worker_converts_then_replies_with_webm_sticker(monkeypatch, tmp_path):
    from services import distortion_stickers as stickers

    bot = _FakeBot()
    input_path = tmp_path / "input.tgs"
    input_path.write_bytes(b"input")
    seen = {}

    async def fake_convert(input_file, output_file):
        seen["converted"] = output_file
        with open(output_file, "wb") as file:
            file.write(b"converted")
        return True

    async def fake_video(input_file, output_file, intensity):
        seen["video_input"] = input_file
        with open(output_file, "wb") as file:
            file.write(b"output")
        return True

    monkeypatch.setattr(stickers, "create_distorted_video_sticker", fake_video)

    asyncio.run(
        stickers.distortion_sticker_worker_async(
            "token",
            123,
            {"media_type": "sticker_tgs", "local_path": str(input_path)},
            45,
            distortion_module=_fake_distortion_module(bot, convert_tgs=fake_convert),
        )
    )

    assert seen["video_input"] == seen["converted"]
    assert len(bot.calls) == 1
    assert bot.calls[0][0] == "sticker"
    assert "_out.webm" in bot.calls[0][2]


def test_installer_delegates_non_stickers_and_intercepts_stickers(monkeypatch):
    from services import distortion_stickers as stickers

    calls = []

    async def original_worker(bot_token, chat_id, media_info, intensity):
        calls.append(("original", media_info["media_type"]))

    module = SimpleNamespace(
        distortion_worker_async=original_worker,
        _sticker_output_installed=False,
    )

    async def fake_sticker_worker(bot_token, chat_id, media_info, intensity, **kwargs):
        calls.append(("sticker", media_info["media_type"]))

    monkeypatch.setattr(stickers, "distortion_sticker_worker_async", fake_sticker_worker)
    stickers.install_into_distortion(module)

    asyncio.run(module.distortion_worker_async("token", 1, {"media_type": "photo"}, 45))
    asyncio.run(module.distortion_worker_async("token", 1, {"media_type": "sticker_video"}, 45))

    assert calls == [("original", "photo"), ("sticker", "sticker_video")]
