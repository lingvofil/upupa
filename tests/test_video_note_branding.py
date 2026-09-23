import asyncio
from pathlib import Path

from PIL import Image

from services import video_note_branding as branding


def _write_mascot(path: Path) -> None:
    Image.new("RGB", (80, 80), (230, 120, 30)).save(path, "PNG")


def test_branding_plate_keeps_circle_transparent_and_replaces_exterior(tmp_path):
    mascot = tmp_path / "mascot.png"
    _write_mascot(mascot)

    plate = branding.build_branding_plate(mascot_path=mascot, size=512)

    assert plate.mode == "RGBA"
    assert plate.getpixel((256, 256))[3] == 0
    assert plate.getpixel((0, 0))[3] == 255
    assert plate.getpixel((511, 511))[3] == 255


def test_prepare_video_note_builds_ffmpeg_overlay(monkeypatch, tmp_path):
    mascot = tmp_path / "mascot.png"
    output = tmp_path / "branded.mp4"
    _write_mascot(mascot)
    captured = {}

    async def fake_run(command):
        captured["command"] = command
        output.write_bytes(b"branded")
        return True, ""

    monkeypatch.setattr(branding, "_run_ffmpeg", fake_run)

    result = asyncio.run(
        branding.prepare_video_note_for_processing(
            "input.mp4",
            str(output),
            mascot_path=mascot,
        )
    )

    assert result == str(output)
    command = captured["command"]
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "scale2ref" in filter_complex
    assert "overlay=0:0" in filter_complex
    map_positions = [i for i, value in enumerate(command) if value == "-map"]
    assert [command[i + 1] for i in map_positions] == ["[v]", "0:a?"]
    assert not Path(str(output) + ".brand.png").exists()


def test_repo_video_note_mascot_asset_is_decodable():
    assert branding.MASCOT_PATH.is_file()

    with Image.open(branding.MASCOT_PATH) as source:
        image_format = source.format
        image_size = source.size
        source.load()

    assert image_format == "JPEG"
    assert min(image_size) >= 64

    plate = branding.build_branding_plate(size=512)
    assert plate.getbbox() is not None
