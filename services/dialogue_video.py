"""Render a vertical replay of a short conversation with Pillow and FFmpeg."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import json
import logging
import os
from pathlib import Path
from random import Random, sample, uniform
import re
import shutil
import wave

from PIL import Image, ImageColor, ImageDraw, ImageFont
from pytz import timezone

from core.paths import DIALOGUE_BACKGROUND_MUSIC_PATH
from core.settings import ELEVENLABS_API_KEY
from infrastructure.subprocess_runner import run_subprocess
from services.elevenlabs import ElevenLabsError, ElevenLabsNoVoicesError, get_elevenlabs_client
from services.speech import SpeechSynthesisError, _pcm_to_wav_bytes, synthesize_pooled_gemini_wav

WIDTH, HEIGHT = 720, 1280
_COLORS = ("#81E0FC", "#FFAA88", "#C8AAFF", "#87E2B2", "#FFD58B", "#F6A8DB", "#A5B5FB")
_GEMINI_VOICES = ("Kore", "Charon", "Aoede", "Fenrir", "Leda", "Orus", "Puck")
_LOCAL_VOICES = (
    ("ru+m3", 42, 165), ("ru+f2", 59, 178), ("ru+m1", 34, 154),
    ("ru+f4", 68, 165), ("ru+m5", 52, 184), ("ru+f1", 53, 158),
    ("ru+m2", 46, 174),
)
_MOSCOW = timezone("Europe/Moscow")
_logger = logging.getLogger(__name__)
_MEDIA_DURATION = re.compile(r"Duration:\s+(\d+):(\d+):(\d+(?:\.\d+)?)")
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
    reply_to_id: int | None = None


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


def _shorten(text, font, width):
    if font.getlength(text) <= width:
        return text
    while text and font.getlength(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _gradient(width, height, top, bottom):
    start, end = ImageColor.getrgb(top), ImageColor.getrgb(bottom)
    gradient = Image.new("RGB", (1, height))
    gradient.putdata([
        tuple(round(first + (last - first) * y / max(1, height - 1)) for first, last in zip(start, end))
        for y in range(height)
    ])
    return gradient.resize((width, height))


def _chat_background(chat_title: str) -> Image.Image:
    base = _gradient(WIDTH, HEIGHT, "#130B17", "#070B16")
    draw = ImageDraw.Draw(base)

    rng = Random(724)
    for y in range(110, HEIGHT, 71):
        for x in range(12, WIDTH, 73):
            left, top = x + rng.randint(-18, 18), y + rng.randint(-18, 18)
            motif = rng.randrange(5)
            palette = ("#35213A", "#282038", "#33284A") if y < 640 else ("#23233B", "#252439", "#202D45")
            ink = rng.choice(palette)
            if motif == 0:
                draw.line([(left, top + 10), (left + 27, top), (left + 19, top + 25),
                           (left + 13, top + 15), (left, top + 10)], fill=ink, width=2, joint="curve")
                draw.line((left + 13, top + 15, left + 27, top), fill=ink, width=2)
            elif motif == 1:
                draw.rounded_rectangle((left, top, left + 30, top + 23), radius=8, outline=ink, width=2)
                draw.line([(left + 7, top + 21), (left + 7, top + 29), (left + 15, top + 23)], fill=ink, width=2)
            elif motif == 2:
                draw.line([(left, top + 10), (left + 8, top + 8), (left + 10, top),
                           (left + 12, top + 8), (left + 20, top + 10), (left + 12, top + 12),
                           (left + 10, top + 20), (left + 8, top + 12), (left, top + 10)], fill=ink, width=2)
            elif motif == 3:
                draw.ellipse((left + 4, top, left + 27, top + 23), outline=ink, width=2)
                draw.arc((left, top + 6, left + 33, top + 19), 0, 355, fill=ink, width=2)
            else:
                draw.rounded_rectangle((left + 5, top + 2, left + 24, top + 22), radius=7, outline=ink, width=2)
                draw.arc((left, top + 8, left + 29, top + 28), 0, 180, fill=ink, width=2)
                draw.line((left + 14, top + 29, left + 14, top + 34), fill=ink, width=2)
                draw.line((left + 8, top + 34, left + 20, top + 34), fill=ink, width=2)
    draw.rounded_rectangle((31, 27, WIDTH - 31, 113), radius=39, fill="#241D2E", outline="#3E3248", width=2)
    draw.ellipse((47, 42, 103, 98), fill="#6744AF")
    for x, height in ((62, 12), (69, 20), (76, 29), (83, 18), (90, 10)):
        draw.rounded_rectangle((x - 1, 70 - height // 2, x + 1, 70 + height // 2), radius=1, fill="#F3E7FF")
    title = _font(31, bold=True)
    heading = _shorten(chat_title or "Диалог", title, 530)
    draw.text((119, 70), heading, fill="#FCF4FF", font=title, anchor="lm")
    return base


def _draw_frames(lines: list[Line], directory: Path, chat_title: str) -> None:
    body, name, small = _font(31), _font(23, bold=True), _font(18)
    quote_font, quote_name = _font(22), _font(21, bold=True)
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
                draw.text((x, y), part, fill="#FCF5FF", font=body)
                x += body.getlength(part)

    base = _chat_background(chat_title)
    colors = {
        author_id: _COLORS[index % len(_COLORS)]
        for index, author_id in enumerate(dict.fromkeys(line.author_id for line in lines))
    }
    wrapped = [_wrap(line.text, body_width, 522) for line in lines]
    replies = []
    earlier = {}
    for line in lines:
        replies.append(earlier.get(line.reply_to_id))
        earlier[line.message_id] = line
    right_author = lines[-1].author_id

    for index in range(len(lines)):
        frame = base.copy()
        draw = ImageDraw.Draw(frame)
        draw.rounded_rectangle((31, 1237, 689, 1242), radius=2, fill="#342940")
        draw.rounded_rectangle((31, 1237, 31 + 658 * (index + 1) // len(lines), 1242), radius=2, fill="#AA7BFA")
        visible = []
        remaining = HEIGHT - 215
        for pos in range(index, -1, -1):
            line, rows = lines[pos], wrapped[pos]
            same_author_before = pos > 0 and lines[pos - 1].author_id == line.author_id
            footer = 23 if body_width(rows[-1]) > 451 else 0
            height = 29 + 38 * len(rows) + footer + (0 if same_author_before else 28)
            if replies[pos]:
                height += 73
            if height + 12 > remaining:
                break
            visible.append((pos, height, same_author_before, footer))
            remaining -= height + (4 if same_author_before else 12)
        y = 147
        for pos, height, same_author_before, footer in reversed(visible):
            line, rows = lines[pos], wrapped[pos]
            reply = replies[pos]
            last_in_group = pos == index or lines[pos + 1].author_id != line.author_id
            active = pos == index
            right_aligned = line.author_id == right_author
            color = colors[line.author_id]
            author = _shorten(line.author, name, 480)
            time = line.date.astimezone(_MOSCOW).strftime("%H:%M")
            show_author = not same_author_before or pos == visible[-1][0]
            if show_author and same_author_before:
                height += 28
            text_width = max(max(map(body_width, rows)), name.getlength(author) if show_author else 0)
            if not footer:
                text_width = max(text_width, body_width(rows[-1]) + 70)
            if reply:
                text_width = max(text_width, min(522, max(260, quote_font.getlength(reply.text) + 31)))
            width = round(text_width) + 36
            left = WIDTH - 38 - width if right_aligned else 83
            right = left + width
            if right_aligned:
                top_color, bubble = ("#9948D8", "#4D55DB") if active else ("#8338C2", "#4447B7")
                mask = Image.new("L", (width + 1, height + 1))
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, width, height), radius=25, fill=255)
                frame.paste(_gradient(width + 1, height + 1, top_color, bubble), (left, y), mask)
                if last_in_group:
                    draw.polygon([(right, y + height - 19), (right - 12, y + height - 6),
                                  (right + 9, y + height - 5)], fill=bubble)
            else:
                bubble = "#382842" if active else "#2D2332"
                draw.rounded_rectangle((left, y, right, y + height), radius=25, fill=bubble)
            if last_in_group:
                if not right_aligned:
                    draw.polygon([(left, y + height - 18), (left + 14, y + height - 6),
                                  (left - 9, y + height - 4)], fill=bubble)
                    initial = next((char.upper() for char in line.author if char.isalnum()), "•")
                    draw.ellipse((21, y + height - 47, 66, y + height - 2), fill="#36264A", outline=color, width=2)
                    draw.text((44, y + height - 24), initial, fill=color, font=name, anchor="mm")
            text_left = left + 18
            if show_author:
                draw.text((text_left, y + 10), author, fill="#F4DFFF" if right_aligned else color, font=name)
            body_y = y + (37 if show_author else 9)
            if reply:
                quote_color = "#EAD3FF" if right_aligned else colors[reply.author_id]
                panel = "#5F38A2" if right_aligned else "#403047"
                draw.rounded_rectangle((text_left, body_y, right - 18, body_y + 63), radius=9, fill=panel)
                draw.rounded_rectangle((text_left, body_y, text_left + 4, body_y + 63), radius=2, fill=quote_color)
                quote_width = width - 59
                draw.text((text_left + 14, body_y + 6), _shorten(reply.author, quote_name, quote_width),
                          fill=quote_color, font=quote_name)
                draw.text((text_left + 14, body_y + 32), _shorten(reply.text, quote_font, quote_width),
                          fill="#F6EAF9", font=quote_font)
                body_y += 73
            for row_index, row in enumerate(rows):
                draw_body(frame, draw, text_left, body_y + 38 * row_index, row)
            draw.text((right - 15, y + height - 11), time, fill="#DDCBE5", font=small, anchor="rd")
            y += height + (12 if last_in_group else 4)
        frame.save(directory / f"frame-{index}.png")


async def _espeak_utterances(lines: list[Line], directory: Path) -> list[bytes]:
    executable = shutil.which("espeak-ng") or shutil.which("espeak")
    if executable is None:
        return []
    voices = dict(zip(dict.fromkeys(line.author_id for line in lines), _LOCAL_VOICES))
    wavs = []
    source = directory / "speech.txt"
    clip = directory / "speech.wav"
    for line in lines:
        source.write_text(line.text, encoding="utf-8")
        variant, pitch, speed = voices[line.author_id]
        result = await run_subprocess(
            [executable, "-v", variant, "-p", str(pitch), "-s", str(speed), "-b", "1",
             "-z", "-w", str(clip), "-f", str(source)],
            timeout=4,
        )
        if not result.ok:
            raise SpeechSynthesisError("Local speech synthesis process failed")
        wavs.append(clip.read_bytes())
    return wavs


def _write_soundtrack(wavs: list[bytes], directory: Path) -> list[float]:
    durations = []
    with wave.open(str(directory / "voice.wav"), "wb") as voice:
        for index, wav_bytes in enumerate(wavs):
            with wave.open(BytesIO(wav_bytes), "rb") as spoken:
                if index == 0:
                    voice.setparams(spoken.getparams())
                rate = spoken.getframerate()
                if (spoken.getnchannels(), spoken.getsampwidth(), rate) != (1, 2, voice.getframerate()):
                    raise ValueError("Unexpected speech WAV format")
                frames = spoken.getnframes()
                if not frames:
                    raise ValueError("Empty speech WAV")
                pause = 1.12 if index == len(wavs) - 1 else 0.28
                duration = round(max(1.28, frames / rate + pause) * 25) / 25
                voice.writeframesraw(spoken.readframes(frames))
                voice.writeframesraw(b"\0\0" * max(0, round(duration * rate) - frames))
                durations.append(duration)
    return durations


async def _elevenlabs_utterances(lines: list[Line]) -> list[bytes]:
    client = get_elevenlabs_client(ELEVENLABS_API_KEY)
    available = await client.get_available_voices()
    if not available:
        raise ElevenLabsNoVoicesError("No account voices for dialogue")
    authors = dict.fromkeys(line.author_id for line in lines)
    voices = sample(available, min(len(authors), len(available)))
    voice_ids = {author: voices[i % len(voices)].voice_id for i, author in enumerate(authors)}
    return [
        _pcm_to_wav_bytes(await client.text_to_speech(voice_ids[line.author_id], line.text))
        for line in lines
    ]


async def _voice(lines: list[Line], directory: Path) -> list[float] | None:
    voices = dict(zip(dict.fromkeys(line.author_id for line in lines), _GEMINI_VOICES))
    try:
        async with asyncio.timeout(50):
            wavs = [
                await synthesize_pooled_gemini_wav(
                    "Прочитай дословно только реплику в кавычках, без пояснений: "
                    + json.dumps(line.text, ensure_ascii=False),
                    voice_name=voices[line.author_id],
                )
                for line in lines
            ]
        return _write_soundtrack(wavs, directory)
    except (EOFError, OSError, SpeechSynthesisError, ValueError, wave.Error) as exc:
        _logger.warning("Gemini dialogue TTS unavailable (%s); trying ElevenLabs", type(exc).__name__)

    try:
        async with asyncio.timeout(35):
            wavs = await _elevenlabs_utterances(lines)
        return _write_soundtrack(wavs, directory)
    except (EOFError, OSError, ElevenLabsError, ValueError, wave.Error) as exc:
        _logger.warning("ElevenLabs dialogue TTS unavailable (%s); trying eSpeak", type(exc).__name__)

    try:
        wavs = await _espeak_utterances(lines, directory)
        return _write_soundtrack(wavs, directory) if wavs else None
    except (EOFError, OSError, SpeechSynthesisError, ValueError, wave.Error) as exc:
        _logger.warning("Local dialogue TTS unavailable (%s); rendering captions only", type(exc).__name__)
        return None


async def _mix_background(track: Path, voice: Path | None, directory: Path, duration: float) -> Path | None:
    """Mix a random part of the configured track, keeping speech in front."""
    if not track.is_file():
        return voice

    probe = await run_subprocess(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(track), "-map", "0:a:0", "-t", "0", "-f", "null", "-"],
        timeout=8,
    )
    match = _MEDIA_DURATION.search(probe.stderr_text)
    if not probe.ok or not match:
        _logger.warning("Could not read dialogue background music duration")
        return voice
    hours, minutes, seconds = match.groups()
    track_duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if track_duration <= 0:
        return voice

    start = uniform(0, max(0, track_duration - duration))
    inputs = []
    if track_duration < duration:
        inputs += ["-stream_loop", "-1"]
    inputs += ["-ss", f"{start:.3f}", "-i", str(track)]
    fade = min(0.6, duration / 3)
    music = (
        f"[0:a:0]aresample=44100,aformat=channel_layouts=stereo,volume=-9dB,"
        f"afade=t=in:d=0.22,afade=t=out:st={duration - fade:.3f}:d={fade:.3f}"
    )
    if voice:
        inputs += ["-i", str(voice)]
        filters = (
            music + "[music];"
            "[1:a:0]aresample=44100,aformat=channel_layouts=stereo,asplit=2[guide][speech];"
            "[music][guide]sidechaincompress=threshold=0.03:ratio=5:attack=10:release=250[ducked];"
            "[speech][ducked]amix=inputs=2:duration=first:normalize=0,"
            "alimiter=limit=0.94:level=0:latency=1[mix]"
        )
    else:
        filters = music + "[mix]"

    output = directory / "soundtrack.wav"
    mixed = await run_subprocess(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *inputs,
         "-filter_complex", filters, "-map", "[mix]", "-t", f"{duration:.3f}", str(output)],
        timeout=20,
    )
    if mixed.ok:
        try:
            with wave.open(str(output), "rb") as audio:
                if audio.getnframes():
                    return output
        except (EOFError, OSError, wave.Error):
            pass
    _logger.warning("Could not mix dialogue background music; keeping the original audio")
    return voice


async def render_dialogue_video(lines: list[Line], directory: Path, *, chat_title: str = "") -> Path:
    frames = asyncio.create_task(asyncio.to_thread(_draw_frames, lines, directory, chat_title))
    try:
        durations = await _voice(lines, directory)
    finally:
        # A canceled handler must wait for Pillow before cleaning up its workspace.
        try:
            await asyncio.shield(frames)
        except asyncio.CancelledError:
            await frames
            raise

    voice = directory / "voice.wav" if durations is not None else None
    if durations is None:
        durations = [min(5.6, max(1.6, round((0.9 + len(line.text) / 22) * 25) / 25)) for line in lines]
        durations[-1] += 1.0
    soundtrack = await _mix_background(DIALOGUE_BACKGROUND_MUSIC_PATH, voice, directory, sum(durations))
    audio = ["-i", str(soundtrack), "-c:a", "aac", "-b:a", "112k"] if soundtrack else []

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
            "-vf", "fps=25", "-frames:v", str(round(sum(durations) * 25)),
            "-c:v", "libx264", "-threads", "2",
            "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ],
        timeout=30,
    )
    if not result.ok or not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg could not render dialogue video (exit={result.returncode})")
    return output
