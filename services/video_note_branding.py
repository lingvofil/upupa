"""Shared branding preprocessor for Telegram video notes.

Telegram stores video notes as square MP4 files and applies the circular crop in
the client. The square area outside that crop can contain Telegram service
branding. Before effects are applied, this module replaces only that exterior
area with Upupa branding so both YTP and distortion use the same clean source.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MASCOT_PATH = PROJECT_ROOT / "assets" / "video_note" / "upupa_mascot.jpg"
FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "Impact.ttf"

BRAND_TEXT = "@expertyebaniebot"
PLATE_SIZE = 512
MASK_SUPERSAMPLE = 4

# Telegram's baked service marks are not confined to the square corners. They
# sit on the lower perimeter of the source frame, including a narrow band just
# inside the client-visible circle. Cover only those two lower sectors plus the
# area outside the circle; leave the rest of the video untouched.
BRANDING_INNER_RADIUS_RATIO = 0.39
BRANDING_SECTORS_DEG = ((18.0, 88.0), (92.0, 168.0))


def _load_font(size: int):
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _branding_mask(size: int) -> Image.Image:
    """Return an antialiased mask for the exterior and Telegram watermark zones."""
    scale = MASK_SUPERSAMPLE
    hi_size = size * scale

    exterior = Image.new("L", (hi_size, hi_size), 255)
    ImageDraw.Draw(exterior).ellipse((0, 0, hi_size - 1, hi_size - 1), fill=0)

    rim = Image.new("L", (hi_size, hi_size), 0)
    rim_draw = ImageDraw.Draw(rim)
    circle_box = (0, 0, hi_size - 1, hi_size - 1)
    for start_deg, end_deg in BRANDING_SECTORS_DEG:
        rim_draw.pieslice(circle_box, start=start_deg, end=end_deg, fill=255)

    center = hi_size / 2
    inner_radius = hi_size * BRANDING_INNER_RADIUS_RATIO
    rim_draw.ellipse(
        (
            center - inner_radius,
            center - inner_radius,
            center + inner_radius,
            center + inner_radius,
        ),
        fill=0,
    )

    mask = ImageChops.lighter(exterior, rim)
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def _round_mascot(path: Path, size: int) -> Image.Image:
    with Image.open(path) as source:
        icon = ImageOps.fit(
            source.convert("RGBA"),
            (size, size),
            method=Image.Resampling.LANCZOS,
        )

    alpha = Image.new("L", (size, size), 0)
    ImageDraw.Draw(alpha).ellipse((0, 0, size - 1, size - 1), fill=255)
    icon.putalpha(alpha)
    return icon


def _draw_arc_text(layer: Image.Image, size: int) -> None:
    """Draw the bot handle along the lower-right watermark arc."""
    font_size = max(16, round(size * 0.039))
    font = _load_font(font_size)
    center = size / 2
    radius = size * 0.44
    start_deg = 78.0
    end_deg = 24.0

    for index, char in enumerate(BRAND_TEXT):
        progress = index / max(1, len(BRAND_TEXT) - 1)
        angle = start_deg + (end_deg - start_deg) * progress

        bbox = font.getbbox(char, stroke_width=1)
        glyph_w = max(1, bbox[2] - bbox[0] + 8)
        glyph_h = max(1, bbox[3] - bbox[1] + 8)
        glyph = Image.new("RGBA", (glyph_w, glyph_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(glyph)
        draw.text(
            (4 - bbox[0], 4 - bbox[1]),
            char,
            font=font,
            fill=(248, 248, 248, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 220),
        )

        # Tangent to the circle. The image Y axis points down, hence this sign.
        rotated = glyph.rotate(
            angle - 90.0,
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )
        radians = math.radians(angle)
        x = center + radius * math.cos(radians) - rotated.width / 2
        y = center + radius * math.sin(radians) - rotated.height / 2
        layer.alpha_composite(rotated, (round(x), round(y)))


def build_branding_plate(
    *,
    mascot_path: str | os.PathLike[str] | None = None,
    size: int = PLATE_SIZE,
) -> Image.Image:
    """Create an RGBA overlay that replaces Telegram's perimeter branding only."""
    mascot = Path(mascot_path) if mascot_path is not None else MASCOT_PATH
    if not mascot.is_file():
        raise FileNotFoundError(f"Upupa video-note mascot asset is missing: {mascot}")

    branding_mask = _branding_mask(size)
    artwork = Image.new("RGBA", (size, size), (18, 18, 20, 255))

    icon_size = max(48, round(size * 0.125))
    icon = _round_mascot(mascot, icon_size)
    center = size / 2
    icon_radius = size * 0.44
    icon_angle = math.radians(132.0)
    icon_x = round(center + icon_radius * math.cos(icon_angle) - icon_size / 2)
    icon_y = round(center + icon_radius * math.sin(icon_angle) - icon_size / 2)
    ImageDraw.Draw(artwork).ellipse(
        (
            icon_x - 2,
            icon_y - 2,
            icon_x + icon_size + 1,
            icon_y + icon_size + 1,
        ),
        fill=(245, 245, 245, 255),
    )
    artwork.alpha_composite(icon, (icon_x, icon_y))
    _draw_arc_text(artwork, size)

    transparent = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    return Image.composite(artwork, transparent, branding_mask)


async def _run_ffmpeg(command: list[str]) -> tuple[bool, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    output = (stderr or stdout).decode(errors="ignore").strip()
    return process.returncode == 0, output


async def prepare_video_note_for_processing(
    input_path: str,
    output_path: str,
    *,
    mascot_path: str | os.PathLike[str] | None = None,
) -> str:
    """Replace the square exterior around a Telegram video note and return the new MP4 path."""
    plate_path = f"{output_path}.brand.png"
    build_branding_plate(mascot_path=mascot_path).save(plate_path, "PNG")

    filter_complex = (
        "[1:v][0:v]scale2ref=w=main_w:h=main_h[brand][base];"
        "[base][brand]overlay=0:0:format=auto:shortest=1[v]"
    )
    command = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-loop",
        "1",
        "-i",
        plate_path,
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-map_metadata",
        "0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        "-shortest",
        output_path,
    ]

    try:
        ok, details = await _run_ffmpeg(command)
        if not ok:
            logging.error("[video_note_branding] FFmpeg failed: %s", details)
            raise RuntimeError("Не удалось заменить оформление видеокружка.")
        logging.info(
            "[video_note_branding] applied input=%s output=%s output_bytes=%s",
            input_path,
            output_path,
            os.path.getsize(output_path) if os.path.exists(output_path) else 0,
        )
        return output_path
    finally:
        try:
            os.remove(plate_path)
        except FileNotFoundError:
            pass
