"""Pillow cards for relationships and dossiers.

The card shows the deterministic facts at a glance (level, meters, weekly
activity bars, archetype, role, faction); the caption keeps the AI comment and
clickable Chronicle links.  Renderers are pure functions of plain data so they
are easy to test and never touch Telegram.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
import logging
import time
from typing import Sequence

from PIL import Image, ImageDraw, ImageOps

from features.social_graph.rendering import _load_font, _sanitize_label_for_font


CARD_WIDTH = 1200
CARD_HEIGHT = 720
AVATAR_CACHE_TTL_SECONDS = 6 * 3600
AVATAR_CACHE_SIZE = 256

INK = (32, 33, 38)
MUTED = (110, 112, 122)
TRACK = (226, 227, 232)
WARM = (234, 96, 92)
COOL = (66, 133, 244)
GREEN = (52, 168, 103)
GOLD = (240, 168, 48)
_AVATAR_PALETTE = ((66, 133, 244), (234, 88, 82), (52, 168, 103), (156, 102, 214), (38, 182, 196), (232, 120, 186), (240, 168, 48))


# --- avatars ------------------------------------------------------------------

_avatar_cache: OrderedDict[int, tuple[float, bytes | None]] = OrderedDict()


async def fetch_avatar(bot, user_id: int) -> bytes | None:
    """Smallest-but-sharp profile photo of a user, cached; None if hidden/unavailable."""
    if bot is None:
        return None
    now = time.monotonic()
    cached = _avatar_cache.get(int(user_id))
    if cached is not None and now - cached[0] < AVATAR_CACHE_TTL_SECONDS:
        return cached[1]
    data: bytes | None = None
    try:
        photos = await bot.get_user_profile_photos(int(user_id), limit=1)
        if photos and photos.photos:
            sizes = photos.photos[0]
            size = next((item for item in sizes if min(item.width, item.height) >= 320), sizes[-1])
            buffer = await bot.download(size.file_id)
            data = buffer.getvalue() if buffer is not None else None
    except Exception as exc:
        logging.info("[social_graph] avatar unavailable user=%s: %s", user_id, exc)
    _avatar_cache[int(user_id)] = (now, data)
    _avatar_cache.move_to_end(int(user_id))
    while len(_avatar_cache) > AVATAR_CACHE_SIZE:
        _avatar_cache.popitem(last=False)
    return data


def _initials(name: str) -> str:
    parts = [part for part in str(name or "").replace("@", " ").split() if part]
    letters = "".join(part[0] for part in parts[:2]).upper()
    return letters or "?"


def _avatar_image(avatar: bytes | None, name: str, user_id: int, size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    if avatar:
        try:
            picture = ImageOps.fit(Image.open(BytesIO(avatar)).convert("RGB"), (size, size))
            picture.putalpha(mask)
            return picture
        except Exception:
            pass
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    color = _AVATAR_PALETTE[abs(int(user_id)) % len(_AVATAR_PALETTE)]
    draw.ellipse((0, 0, size - 1, size - 1), fill=(*color, 255))
    font = _load_font(int(size * 0.38))
    text = _sanitize_label_for_font(_initials(name), font) or "?"
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]), text, font=font, fill=(255, 255, 255, 255))
    tile.putalpha(mask)
    return tile


# --- drawing helpers ----------------------------------------------------------

def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = _sanitize_label_for_font(" ".join(str(text or "").split()), font)
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int = 2) -> list[str]:
    words = _sanitize_label_for_font(" ".join(str(text or "").split()), font).split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    consumed = " ".join(lines).split()
    if len(consumed) < len(words) and lines:
        lines[-1] = _fit(draw, lines[-1] + " …", font, max_width)
    return [_fit(draw, line, font, max_width) for line in lines]


def _centered(draw: ImageDraw.ImageDraw, center_x: float, y: float, text: str, font, fill=INK, max_width: int = 400) -> None:
    text = _fit(draw, text, font, max_width)
    width = draw.textlength(text, font=font)
    draw.text((center_x - width / 2, y), text, font=font, fill=fill)


def _background(tint: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), (250, 249, 246))
    overlay = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for step in range(0, 260, 4):
        alpha = int(46 * (1 - step / 260))
        draw.rectangle((0, step, CARD_WIDTH, step + 4), fill=(*tint, alpha))
    image.paste(overlay, (0, 0), overlay)
    return image


def _badge(draw: ImageDraw.ImageDraw, center_x: float, y: float, text: str, color, font) -> None:
    text = _fit(draw, text, font, 640)
    width = draw.textlength(text, font=font)
    pad_x, height = 22, 46
    draw.rounded_rectangle(
        (center_x - width / 2 - pad_x, y, center_x + width / 2 + pad_x, y + height),
        radius=height // 2,
        fill=(*color, 255),
    )
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((center_x - width / 2, y + (height - (box[3] - box[1])) / 2 - box[1]), text, font=font, fill=(255, 255, 255))


def _meter(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, label: str, value: float, color, font, value_text: str) -> None:
    draw.text((x, y), label, font=font, fill=MUTED)
    value_width = draw.textlength(value_text, font=font)
    draw.text((x + width - value_width, y), value_text, font=font, fill=INK)
    bar_y = y + 36
    draw.rounded_rectangle((x, bar_y, x + width, bar_y + 14), radius=7, fill=TRACK)
    filled = max(0.0, min(1.0, value))
    if filled > 0:
        draw.rounded_rectangle((x, bar_y, x + max(14, int(width * filled)), bar_y + 14), radius=7, fill=color)


def _weekly_bars(draw: ImageDraw.ImageDraw, x: int, y: int, width: int, height: int, values: Sequence[int], color) -> None:
    values = list(values) or [0]
    peak = max(values) or 1
    gap = 8
    bar_width = (width - gap * (len(values) - 1)) / len(values)
    for index, value in enumerate(values):
        left = x + index * (bar_width + gap)
        bar_height = max(4, int(height * value / peak)) if value else 4
        fill = color if value else TRACK
        draw.rounded_rectangle((left, y + height - bar_height, left + bar_width, y + height), radius=4, fill=fill)


def _ring(draw: ImageDraw.ImageDraw, center, radius: int, fraction: float, color, width: int = 16) -> None:
    cx, cy = center
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    draw.ellipse(box, outline=TRACK, width=width)
    if fraction > 0:
        draw.arc(box, start=-90, end=-90 + 360 * max(0.0, min(1.0, fraction)), fill=color, width=width)


def _to_png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


# --- pair card ----------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PairCardData:
    user_a_id: int
    user_b_id: int
    user_a_name: str
    user_b_name: str
    level: int
    archetype: str
    affinity: int
    tension: int
    reciprocity: float
    trend: str
    weekly: tuple[int, ...] = ()
    phase: str | None = None
    main_event: str | None = None
    avatar_a: bytes | None = None
    avatar_b: bytes | None = None


def render_pair_card(data: PairCardData) -> bytes:
    tint = WARM if data.tension >= 45 else (GREEN if data.affinity >= 60 else COOL)
    image = _background(tint)
    draw = ImageDraw.Draw(image, "RGBA")
    title_font, name_font, label_font, big_font, small_font = (
        _load_font(30), _load_font(34), _load_font(26), _load_font(72), _load_font(24)
    )

    avatar_size = 190
    for center_x, user_id, name, avatar in (
        (230, data.user_a_id, data.user_a_name, data.avatar_a),
        (CARD_WIDTH - 230, data.user_b_id, data.user_b_name, data.avatar_b),
    ):
        tile = _avatar_image(avatar, name, user_id, avatar_size)
        image.paste(tile, (int(center_x - avatar_size / 2), 50), tile)
        draw.ellipse((center_x - avatar_size / 2 - 4, 46, center_x + avatar_size / 2 + 4, 54 + avatar_size), outline=(*tint, 255), width=6)
        _centered(draw, center_x, 262, name, name_font, max_width=360)

    center = (CARD_WIDTH // 2, 150)
    _ring(draw, center, 100, data.level / 15.0, tint)
    _centered(draw, center[0], center[1] - 56, str(data.level), big_font)
    _centered(draw, center[0], center[1] + 26, "из 15", small_font, fill=MUTED)
    _badge(draw, CARD_WIDTH / 2, 300, data.archetype, tint, title_font)

    meter_y = 386
    meter_width = 330
    for index, (label, value, color, text) in enumerate((
        ("Близость", data.affinity / 100.0, GREEN, f"{data.affinity}/100"),
        ("Напряжение", data.tension / 100.0, WARM, f"{data.tension}/100"),
        ("Взаимность", data.reciprocity, COOL, f"{round(data.reciprocity * 100)}%"),
    )):
        _meter(draw, 70 + index * (meter_width + 45), meter_y, meter_width, label, value, color, label_font, text)

    bars_y = 486
    draw.text((70, bars_y), "Активность по неделям", font=label_font, fill=MUTED)
    status = data.phase or data.trend
    status_text = _fit(draw, f"{status} · {data.trend}" if data.phase and data.phase != data.trend else status, label_font, 560)
    draw.text((CARD_WIDTH - 70 - draw.textlength(status_text, font=label_font), bars_y), status_text, font=label_font, fill=INK)
    _weekly_bars(draw, 70, bars_y + 42, CARD_WIDTH - 140, 90, data.weekly or (0,), tint)

    if data.main_event:
        draw.text((70, 664), _fit(draw, f"Главный эпизод: «{data.main_event}»", small_font, CARD_WIDTH - 140), font=small_font, fill=MUTED)
    return _to_png(image)


async def render_pair_card_async(data: PairCardData) -> bytes:
    return await asyncio.to_thread(render_pair_card, data)


# --- dossier card -------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DossierRelation:
    name: str
    level: int
    archetype: str


@dataclass(frozen=True, slots=True)
class DossierCardData:
    user_id: int
    name: str
    role_title: str | None
    role_blurb: str | None
    faction_name: str | None
    faction_color: tuple[int, int, int] | None
    connections: int
    period_days: int
    relations: tuple[DossierRelation, ...] = ()
    antagonist: DossierRelation | None = None
    antagonist_tension: int = 0
    chronicle_count: int = 0
    latest_event: str | None = None
    avatar: bytes | None = None


def render_dossier_card(data: DossierCardData) -> bytes:
    tint = data.faction_color or GOLD
    image = _background(tint)
    draw = ImageDraw.Draw(image, "RGBA")
    name_font, title_font, label_font, small_font = _load_font(44), _load_font(30), _load_font(26), _load_font(24)

    left_center = 250
    avatar_size = 250
    tile = _avatar_image(data.avatar, data.name, data.user_id, avatar_size)
    image.paste(tile, (int(left_center - avatar_size / 2), 60), tile)
    draw.ellipse((left_center - avatar_size / 2 - 5, 55, left_center + avatar_size / 2 + 5, 65 + avatar_size), outline=(*tint, 255), width=7)
    _centered(draw, left_center, 340, data.name, name_font, max_width=420)
    if data.role_title:
        _badge(draw, left_center, 404, data.role_title, tint, title_font)
    if data.role_blurb:
        for line_index, line in enumerate(_wrap(draw, data.role_blurb, small_font, 420)):
            _centered(draw, left_center, 464 + line_index * 30, line, small_font, fill=MUTED, max_width=420)
    if data.faction_name:
        _centered(draw, left_center, 540, "фракция", small_font, fill=MUTED)
        _centered(draw, left_center, 570, data.faction_name, label_font, max_width=420)
    _centered(draw, left_center, 640, f"связей за {data.period_days} дней: {data.connections}", small_font, fill=MUTED)

    x = 540
    width = CARD_WIDTH - x - 70
    draw.line((x - 40, 60, x - 40, CARD_HEIGHT - 60), fill=TRACK, width=3)
    y = 66
    draw.text((x, y), "Главные отношения", font=title_font, fill=INK)
    y += 56
    if not data.relations:
        draw.text((x, y), "пока не накопились", font=label_font, fill=MUTED)
        y += 60
    for relation in data.relations[:3]:
        draw.text((x, y), _fit(draw, relation.name, label_font, width - 140), font=label_font, fill=INK)
        level_text = f"{relation.level}/15"
        draw.text((x + width - draw.textlength(level_text, font=label_font), y), level_text, font=label_font, fill=INK)
        draw.rounded_rectangle((x, y + 38, x + width, y + 50), radius=6, fill=TRACK)
        draw.rounded_rectangle((x, y + 38, x + max(12, int(width * relation.level / 15)), y + 50), radius=6, fill=tint)
        draw.text((x, y + 58), _fit(draw, relation.archetype, small_font, width), font=small_font, fill=MUTED)
        y += 108

    if data.antagonist is not None:
        y += 6
        draw.text((x, y), "Главный оппонент", font=small_font, fill=MUTED)
        draw.text((x, y + 32), _fit(draw, f"{data.antagonist.name} · напряжение {data.antagonist_tension}/100", label_font, width), font=label_font, fill=WARM)
        y += 82

    chronicle_y = max(y + 10, CARD_HEIGHT - 150)
    draw.text((x, chronicle_y), f"В Летописи: {data.chronicle_count}", font=title_font, fill=INK)
    if data.latest_event:
        draw.text((x, chronicle_y + 46), _fit(draw, f"последнее: «{data.latest_event}»", small_font, width), font=small_font, fill=MUTED)
    return _to_png(image)


async def render_dossier_card_async(data: DossierCardData) -> bytes:
    return await asyncio.to_thread(render_dossier_card, data)
