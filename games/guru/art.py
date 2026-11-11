"""Общее для рисунков салона: рамка в стиле карт Таро, ночное небо и выдача JPEG."""

from __future__ import annotations

import io
import math
import random
from functools import lru_cache
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont

from core.paths import PROJECT_ROOT

Point = tuple[float, float]
Box = tuple[int, int, int, int]

WIDTH, HEIGHT = 738, 1100
SCALE = 2  # рисуем крупнее и уменьшаем: так края гладкие
FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "BerlinSansFBCyrillic-Regular.ttf"


def render_spread(cards: tuple[str, ...]) -> bytes:
    """Весь расклад одним изображением, чтобы обновлять его внутри панели приёма."""
    width, height, gap = 400, 596, 12
    image = Image.new("RGB", (width * len(cards) + gap * (len(cards) - 1), height), (20, 17, 16))
    for index, card in enumerate(cards):
        with Image.open(PROJECT_ROOT / "assets" / "guru_tarot" / f"{card}.jpg") as source:
            image.paste(source.convert("RGB").resize((width, height), Image.Resampling.LANCZOS), (index * (width + gap), 0))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()

PARCHMENT = (214, 190, 148)
FRAME = (20, 17, 16)
GOLD = (222, 178, 96)
GOLD_DIM = (146, 116, 66)
SKY_TOP = (22, 30, 66)
SKY_BOTTOM = (70, 48, 74)
PICTURE = (48, 100, 690, 1040)
TITLE_MARGIN = 96  # по краям заголовка стоят звёзды в кругах


def catmull(points: Sequence[Point], steps: int = 24) -> list[Point]:
    """Плавная кривая через точки."""
    knots = [points[0], *points, points[-1]]
    curve: list[Point] = []
    for i in range(1, len(knots) - 2):
        p0, p1, p2, p3 = knots[i - 1], knots[i], knots[i + 1], knots[i + 2]
        for step in range(steps):
            t = step / steps
            curve.append(tuple(
                0.5 * (2 * p1[axis] + (p2[axis] - p0[axis]) * t + (2 * p0[axis] - 5 * p1[axis] + 4 * p2[axis] - p3[axis]) * t ** 2
                       + (3 * p1[axis] - p0[axis] - 3 * p2[axis] + p3[axis]) * t ** 3)
                for axis in (0, 1)
            ))
    curve.append(points[-1])
    return curve


def closed_curve(points: Sequence[Point]) -> list[Point]:
    knots = [points[-1], *points, points[0], points[1]]
    curve: list[Point] = []
    for i in range(1, len(knots) - 2):
        curve += catmull([knots[i - 1], knots[i], knots[i + 1], knots[i + 2]], 16)[16:32]
    return curve


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size * SCALE)


def _glyph(char: str) -> bytes:
    image = Image.new("L", (40 * SCALE, 40 * SCALE), 0)
    ImageDraw.Draw(image).text((8 * SCALE, 8 * SCALE), char, font=font(12), fill=255)
    return image.tobytes()


@lru_cache(maxsize=4096)
def _has_glyph(char: str) -> bool:
    # Знак, которого нет в шрифте, рисуется той же заглушкой, что и заведомо несуществующий.
    return _glyph(char) != _glyph("\uffff")


def printable(text: str, fallback: str = "") -> str:
    """Оставить то, что шрифт умеет рисовать: в именах из мессенджера бывают эмодзи и чужие письменности."""
    spaced = "".join(" " if char.isspace() else char for char in text)
    return " ".join("".join(char for char in spaced if char == " " or _has_glyph(char)).split()) or fallback


def scaled(points: Iterable[Point]) -> list[Point]:
    return [(x * SCALE, y * SCALE) for x, y in points]


def sky(size: tuple[int, int], rng: random.Random) -> Image.Image:
    width, height = size
    gradient = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(height - 1, 1)
        gradient.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(SKY_TOP, SKY_BOTTOM)))
    gradient = gradient.resize(size)
    draw = ImageDraw.Draw(gradient)
    for _ in range(220):
        x, y = rng.uniform(0, width), rng.uniform(0, height)
        radius = rng.choice((1, 1, 1, 2, 3)) * SCALE * 0.6
        light = rng.randint(150, 255)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(light, light, min(255, light + 20)))
    return gradient


def _draw_frame(image: Image.Image, title: str) -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    draw.rectangle((18 * SCALE, 18 * SCALE, width - 18 * SCALE, height - 18 * SCALE), fill=FRAME)
    for inset, color, line in ((26, GOLD_DIM, 1), (32, GOLD, 2)):
        draw.rectangle((inset * SCALE, inset * SCALE, width - inset * SCALE, height - inset * SCALE), outline=color, width=line * SCALE)
    size = 38
    while size > 20 and draw.textlength(title, font=font(size)) > (WIDTH - 2 * TITLE_MARGIN) * SCALE:
        size -= 2  # длинный заголовок уменьшается, чтобы не наехать на звёзды по углам
    draw.text((width / 2, 66 * SCALE), title, font=font(size), fill=GOLD, anchor="mm")
    for x in (64, WIDTH - 64):
        cx, cy, radius = x * SCALE, 66 * SCALE, 16 * SCALE
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=GOLD, width=2 * SCALE)
        draw.polygon([(cx + math.sin(math.radians(a * 144)) * radius, cy - math.cos(math.radians(a * 144)) * radius) for a in range(5)], outline=GOLD)


def canvas(title: str, rng: random.Random) -> tuple[Image.Image, Image.Image, Box]:
    """Рамка с заголовком, ночной холст и область, куда холст потом вставится."""
    size = (WIDTH * SCALE, HEIGHT * SCALE)
    image = Image.new("RGB", size, PARCHMENT)
    _draw_frame(image, title)
    left, top, right, bottom = PICTURE
    box = (left * SCALE, top * SCALE, right * SCALE, bottom * SCALE)
    picture = Image.new("RGB", size, FRAME)
    picture.paste(sky((box[2] - box[0], box[3] - box[1]), rng), box[:2])
    return image, picture, box


def finish(image: Image.Image, picture: Image.Image, box: Box) -> bytes:
    """Вставить холст в рамку, добавить зерно и отдать JPEG."""
    grain = Image.effect_noise((WIDTH // 2, HEIGHT // 2), 26).resize(image.size, Image.BILINEAR).point(lambda v: max(0, (v - 128) // 3))
    picture.paste((30, 22, 18), mask=grain)
    image.paste(picture.crop(box), box[:2])
    ImageDraw.Draw(image).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), outline=GOLD, width=2 * SCALE)
    output = io.BytesIO()
    image.resize((WIDTH, HEIGHT), Image.LANCZOS).save(output, "JPEG", quality=86, optimize=True, progressive=True)
    return output.getvalue()
