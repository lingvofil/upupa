import asyncio
import math
from pathlib import Path

from PIL import Image

from services import video_note_branding as branding


def _write_mascot(path: Path) -> None:
    Image.new("RGB", (80, 80), (230, 120, 30)).save(path, "PNG")


def _polar_point(size: int, radius_ratio: float, angle_deg: float) -> tuple[int, int]:
    radius = size * radius_ratio
    angle = math.radians(angle_deg)
    center = size / 2
    return (
        round(center + radius * math.cos(angle)),
        round(center + radius * math.sin(angle)),
    )


def test_branding_plate_preserves_entire_video_circle(tmp_path):
    mascot = tmp_path / "mascot.png"
    _write_mascot(mascot)

    plate = branding.build_branding_plate(mascot_path=mascot, size=512)

    assert plate.mode == "RGBA"
    assert plate.getpixel((256, 256))[3] == 0
    assert plate.getpixel((0, 0))[3] == 255
    assert plate.getpixel((511, 511))[3] == 255

    # Regression: the previous mask cut large black wedges into the lower part
    # of the visible video circle. Every sampled point inside the circle must
    # remain transparent now.
    for angle in (35, 48, 90, 132, 145, 270):
        point = _polar_point(512, 0.44, angle)
        assert plate.getpixel(point)[3] == 0


def test_mascot_is_drawn_in_lower_left_exterior(tmp_path):
    mascot = tmp_path / "mascot.png"
    _write_mascot(mascot)

    plate = branding.build_branding_plate(mascot_path=mascot, size=512)
    mascot_center = _polar_point(512, 0.565, 135)

    assert plate.getpixel(mascot_center)[3] > 240
    assert plate.getpixel(mascot_center)[:3] != (18, 18, 20)


def test_prepare_video_note_builds_ffmpeg_overlay(monkeypatch, tmp_path):
    mascot = tmp_path / "mascot.png"
    output = tmp_path / "branded.mp4"
    _write_mascot(mascot)
    captured = {}

    async def fake_probe(_input_path):
        return 384, 384

    async def fake_run(command):
        captured["command"] = command
        plate_path = next(part for part in command if str(part).endswith(".brand.png"))
        with Image.open(plate_path) as plate:
            captured["plate_size"] = plate.size
        output.write_bytes(b"branded")
        return True, ""

    monkeypatch.setattr(branding, "_probe_video_size", fake_probe)
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
    assert captured["plate_size"] == (384, 384)
    assert "scale2ref" not in filter_complex
    assert filter_complex == "[0:v][1:v]overlay=0:0:format=auto:shortest=1[v]"
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
