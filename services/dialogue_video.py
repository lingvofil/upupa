"""Render a vertical replay of a short conversation with Pillow and FFmpeg."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
import os
from pathlib import Path
import re
import shutil
import wave

from PIL import Image, ImageDraw, ImageFont
from pytz import timezone

from infrastructure.subprocess_runner import run_subprocess

WIDTH, HEIGHT = 720, 1280
_COLORS = ("#80E2CC", "#FFB985", "#C9ACFF", "#F6DB83", "#90C6FF")
_MOSCOW = timezone("Europe/Moscow")
_logger = logging.getLogger(__name__)
_EMOJI = re.compile(
    r"([\U0001f1e6-\U0001f1ff]{2}|"
    r"[\u2600-\u27bf\U0001f300-\U0001faff][\ufe0e\ufe0f\U0001f3fb-\U0001f3ff]*"
    r"(?:\u200d[\u2600-\u27bf\U0001f300-\U0001faff][\ufe0e\ufe0f\U0001f3fb-\U0001f3ff]*)*)"
)


@dataclass(frozen=True)
class Line:
    message_id: int
    author_id: int
    author: str
    text: str
    date: datetime


def _font(size: int, *, bold: bool = False):
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = (
        f"DejaVuSans{'-Bold' if bold else ''}.ttf",
        str(windows_fonts / ("segoeuib.ttf" if bold else "segoeui.ttf")),
        str(Path(__file__).resolve().parents[1] / "assets/fonts/BerlinSansFBCyrillic-Regular.ttf"),
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    raise RuntimeError("No font with Cyrillic support found")


def _emoji_font():
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename, size in (("NotoColorEmoji.ttf", 109), (windows_fonts / "seguiemj.ttf", 31)):
        try:
            return ImageFont.truetype(str(filename), size)
        except OSError:
            continue
    return None


def _wrap(text, measure, width):
    rows = [""]
    for word in text.split():
        joined = f"{rows[-1]} {word}".strip()
        if measure(joined) <= width:
            rows[-1] = joined
            continue
        if rows[-1]:
            rows.append("")
        for position, part in enumerate(_EMOJI.split(word)):
            for glyph in (part,) if position % 2 else part:
                if rows[-1] and measure(rows[-1] + glyph) > width:
                    rows.append("")
                rows[-1] += glyph
    return rows


def _draw_frames(lines: list[Line], directory: Path) -> None:
    title, body, name, small = _font(57, bold=True), _font(31), _font(24, bold=True), _font(19)
    emoji_font = _emoji_font()
    emoji_images = {}

    def body_width(text):
        return sum(
            34 if emoji_font and index % 2 else body.getlength(part)
            for index, part in enumerate(_EMOJI.split(text))
        )

    def draw_body(frame, draw, x, y, text):
        for index, part in enumerate(_EMOJI.split(text)):
            if emoji_font and index % 2:
                glyph = emoji_images.get(part)
                if glyph is None:
                    left, top, right, bottom = emoji_font.getbbox(part)
                    glyph = Image.new("RGBA", (right - left, bottom - top))
                    ImageDraw.Draw(glyph).text((-left, -top), part, font=emoji_font, embedded_color=True)
                    glyph.thumbnail((31, 31), Image.Resampling.LANCZOS)
                    emoji_images[part] = glyph
                frame.paste(glyph, (round(x), y + 5), glyph)
                x += 34
            else:
                draw.text((x, y), part, fill="#FAF4E6", font=body)
                x += body.getlength(part)

    base = Image.new("RGB", (WIDTH, HEIGHT), "#101C25")
    background = ImageDraw.Draw(base)
    for y in range(22, HEIGHT, 34):
        for x in range(17, WIDTH, 34):
            background.ellipse((x, y, x + 2, y + 2), fill="#233542")
    background.rectangle((0, 0, WIDTH, 166), fill="#142630")
    background.rectangle((0, 164, WIDTH, 166), fill="#2F535C")
    background.text((40, 28), "УПУПА", fill="#80E2CC", font=small)
    background.text((38, 56), "Диалог", fill="#F7F1DD", font=title)

    colors = {
        author_id: _COLORS[index % len(_COLORS)]
        for index, author_id in enumerate(dict.fromkeys(line.author_id for line in lines))
    }
    layouts = []
    for line in lines:
        rows = _wrap(line.text, body_width, 524)
        height = 77 + len(rows) * 39
        layouts.append((line, rows, height))

    for index in range(len(lines)):
        frame = base.copy()
        draw = ImageDraw.Draw(frame)
        draw.rounded_rectangle((545, 109, 680, 115), radius=3, fill="#2F535C")
        draw.rounded_rectangle((545, 109, 545 + 135 * (index + 1) // len(lines), 115), radius=3, fill="#80E2CC")
        visible = layouts[:index + 1]
        while sum(height + 14 for _, _, height in visible) > HEIGHT - 228:
            visible = visible[1:]
        y = 194
        for line, rows, height in visible:
            color = colors[line.author_id]
            author = _wrap(line.author, name.getlength, 500)[0]
            if author != line.author:
                author = _wrap(line.author, name.getlength, 460)[0].rstrip() + "…"
            time = line.date.astimezone(_MOSCOW).strftime("%H:%M")
            initial = next((char.upper() for char in line.author if char.isalnum()), "•")
            draw.ellipse((38, y + 4, 84, y + 50), fill=color)
            draw.text((61, y + 27), initial, fill="#142630", font=name, anchor="mm")
            draw.rounded_rectangle((99, y, 681, y + height), radius=22, fill="#203540")
            draw.text((122, y + 12), author, fill=color, font=name)
            for row_index, row in enumerate(rows):
                draw_body(frame, draw, 122, y + 43 + 39 * row_index, row)
            draw.text((656, y + height - 11), time, fill="#A0B5BC", font=small, anchor="rd")
            y += height + 14
        frame.save(directory / f"frame-{index}.png")


async def _voice(lines: list[Line], directory: Path) -> list[float] | None:
    executable = shutil.which("espeak-ng") or shutil.which("espeak")
    if executable is None:
        return None

    durations = []
    try:
        with wave.open(str(directory / "voice.wav"), "wb") as voice:
            for index, line in enumerate(lines):
                source = directory / "speech.txt"
                source.write_text(line.text, encoding="utf-8")
                clip = directory / "speech.wav"
                result = await run_subprocess(
                    [executable, "-v", "ru", "-b", "1", "-s", "169", "-w", str(clip), "-f", str(source)],
                    timeout=4,
                )
                if not result.ok:
                    raise RuntimeError("speech synthesis process failed")
                with wave.open(str(clip), "rb") as spoken:
                    if index == 0:
                        voice.setparams(spoken.getparams())
                    if spoken.getnchannels() != 1 or spoken.getsampwidth() != 2:
                        raise ValueError("unexpected speech WAV format")
                    rate = spoken.getframerate()
                    frames = spoken.getnframes()
                    duration = max(1.28, (frames / rate) + (1.12 if index == len(lines) - 1 else 0.28))
                    duration = round(duration * 25) / 25
                    voice.writeframesraw(spoken.readframes(frames))
                    voice.writeframesraw(b"\0\0" * max(0, round(duration * rate) - frames))
                    durations.append(duration)
    except (EOFError, OSError, RuntimeError, ValueError, wave.Error):
        _logger.warning("Dialogue video voice synthesis failed; rendering captions only")
        return None
    return durations


async def render_dialogue_video(lines: list[Line], directory: Path) -> Path:
    frames = asyncio.create_task(asyncio.to_thread(_draw_frames, lines, directory))
    try:
        durations = await _voice(lines, directory)
    finally:
        # A canceled handler must wait for Pillow before cleaning up its workspace.
        try:
            await asyncio.shield(frames)
        except asyncio.CancelledError:
            await frames
            raise

    audio = []
    if durations is None:
        durations = [min(5.6, max(1.6, round((0.9 + len(line.text) / 22) * 25) / 25)) for line in lines]
        durations[-1] += 1.0
    else:
        audio = ["-i", str(directory / "voice.wav"), "-c:a", "aac", "-b:a", "80k"]

    playlist = directory / "frames.ffconcat"
    playlist.write_text(
        "ffconcat version 1.0\n"
        + "".join(f"file 'frame-{index}.png'\nduration {duration:.2f}\n" for index, duration in enumerate(durations))
        + f"file 'frame-{len(lines) - 1}.png'\n",
        encoding="ascii",
    )
    output = directory / "dialogue.mp4"
    result = await run_subprocess(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "concat", "-safe", "1", "-i", str(playlist), *audio,
            "-t", f"{sum(durations):.2f}", "-r", "25", "-c:v", "libx264", "-threads", "2",
            "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ],
        timeout=30,
    )
    if not result.ok or not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg could not render dialogue video (exit={result.returncode})")
    return output
