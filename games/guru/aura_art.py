"""Рисунок ауры для энергетики: силуэт клиента и слои чувств из выпавших толкований.

У каждого чувства свой вид, тот же, что в тексте: вина липнет болотной мутью, страх дрожит
лиловым по краям, надежда стоит золотом над головой, злость летит красными искрами.
Модуль получает ключи чувств и отдаёт JPEG.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, Iterable

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from games.guru import art
from games.guru.art import HEIGHT, SCALE, WIDTH, Point

TITLE = "АУРА КЛИЕНТА"
CENTER = (369, 640)
ASPECT = 0.62  # аура вытянута вверх
SILHOUETTE = (16, 12, 26)
SWAMP = (46, 62, 30)
SMOKE = (20, 16, 42)
RADII = {1: (360,), 2: (455, 320), 3: (480, 375, 270), 4: (495, 410, 320, 235)}
Wobble = Callable[[float], float]


@dataclass(frozen=True)
class Layer:
    label: str
    color: tuple[int, int, int]
    bright: tuple[int, int, int]


LAYERS = {
    "guilt": Layer("Болотная муть", (86, 112, 54), (168, 196, 104)),
    "fear": Layer("Лиловая дрожь", (116, 96, 176), (206, 190, 250)),
    "hope": Layer("Золотой свет", (214, 166, 72), (255, 226, 150)),
    "anger": Layer("Красные искры", (178, 40, 40), (255, 150, 84)),
}


def _contour(radius: float, wobble: Wobble, steps: int = 420) -> list[Point]:
    cx, cy = CENTER
    points = []
    for index in range(steps):
        angle = 2 * math.pi * index / steps
        r = radius * (1 + wobble(angle))
        points.append((cx + math.cos(angle) * r * ASPECT, cy - math.sin(angle) * r))
    return points


def _wobble(key: str, rng: random.Random) -> Wobble:
    a, b, c = rng.uniform(0, 6.3), rng.uniform(0, 6.3), rng.uniform(0, 6.3)
    if key == "guilt":
        return lambda t: 0.08 * math.sin(2 * t + a) + 0.06 * math.sin(5 * t + b) + 0.04 * math.sin(9 * t + c)
    if key == "fear":
        return lambda t: 0.025 * math.sin(7 * t + a) + 0.018 * math.sin(41 * t + b) + 0.012 * math.sin(73 * t + c)
    if key == "hope":
        return lambda t: 0.04 * math.sin(3 * t + a) + 0.025 * math.sin(5 * t + b)
    return lambda t: 0.15 * (1 - abs(((t * 11 / (2 * math.pi) + a) % 1) * 2 - 1)) - 0.06 + 0.02 * math.sin(5 * t + b)


def _blotches(size: tuple[int, int], radius: float, rng: random.Random) -> Image.Image:
    """Вина липнет: тёмные пятна и подтёки внутри слоя."""
    cx, cy = CENTER
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for _ in range(46):
        angle, r = rng.uniform(0, 2 * math.pi), radius * rng.uniform(0.72, 0.98)
        x, y = cx + math.cos(angle) * r * ASPECT, cy - math.sin(angle) * r
        wide, tall = rng.uniform(8, 24), rng.uniform(12, 46)
        draw.ellipse((x - wide, y - tall / 3, x + wide, y + tall), fill=rng.randint(120, 210))
    return mask.filter(ImageFilter.GaussianBlur(3))


def _fear_extras(draw: ImageDraw.ImageDraw, radius: float, rng: random.Random) -> None:
    """Страх дрожит тонкими контурами по краям."""
    for shift in (-22, -10, 12, 26):
        wobble = _wobble("fear", rng)
        points = _contour(radius + shift, wobble)
        draw.line([*points, points[0]], fill=255, width=2, joint="curve")


def _hope_extras(draw: ImageDraw.ImageDraw, radius: float, rng: random.Random) -> None:
    """Надежда расходится лучами вверх: широкими у головы, острыми на конце."""
    cx, cy = CENTER[0], CENTER[1] - 120
    for degrees in range(20, 165, 8):
        angle = math.radians(degrees + rng.uniform(-3, 3))
        spread = math.radians(rng.uniform(3.5, 6.5))
        reach = radius * rng.uniform(0.82, 1.08)
        near = 128
        draw.polygon([
            (cx + math.cos(angle - spread) * near, cy - math.sin(angle - spread) * near),
            (cx + math.cos(angle) * reach * ASPECT * 1.15, cy - math.sin(angle) * reach * 0.82),
            (cx + math.cos(angle + spread) * near, cy - math.sin(angle + spread) * near),
        ], fill=255)


def _anger_extras(draw: ImageDraw.ImageDraw, radius: float, rng: random.Random) -> None:
    """Злость летит искрами наружу."""
    cx, cy = CENTER
    for _ in range(130):
        angle = rng.uniform(0, 2 * math.pi)
        start = radius * rng.uniform(0.88, 1.28)
        length = rng.uniform(10, 36)
        x1, y1 = cx + math.cos(angle) * start * ASPECT, cy - math.sin(angle) * start
        x2, y2 = cx + math.cos(angle) * (start + length) * ASPECT, cy - math.sin(angle) * (start + length)
        draw.line((x1, y1, x2, y2), fill=255, width=rng.choice((2, 2, 3)))


def _upscaled(mask: Image.Image, size: tuple[int, int]) -> Image.Image:
    return mask.resize(size, Image.BILINEAR)


def _paint_layer(picture: Image.Image, key: str, radius: float, rng: random.Random) -> None:
    layer = LAYERS[key]
    size = (WIDTH, HEIGHT)
    contour = _contour(radius, _wobble(key, rng))

    band = Image.new("L", size, 0)
    band_draw = ImageDraw.Draw(band)
    band_draw.polygon(contour, fill=255)
    picture.paste(layer.color, mask=_upscaled(band.filter(ImageFilter.GaussianBlur(9)).point(lambda v: v * 3 // 4), picture.size))
    if key == "guilt":
        picture.paste(SWAMP, mask=_upscaled(_blotches(size, radius, rng), picture.size))

    rim = Image.new("L", size, 0)
    rim_draw = ImageDraw.Draw(rim)
    rim_draw.line([*contour, contour[0]], fill=255, width=4, joint="curve")
    if key == "fear":
        _fear_extras(rim_draw, radius, rng)
    glow = ImageChops.screen(rim.filter(ImageFilter.GaussianBlur(5)), rim)
    picture.paste(layer.bright, mask=_upscaled(glow, picture.size))

    if key in ("hope", "anger"):
        extras = Image.new("L", size, 0)
        extras_draw = ImageDraw.Draw(extras)
        (_hope_extras if key == "hope" else _anger_extras)(extras_draw, radius, rng)
        soft = extras.filter(ImageFilter.GaussianBlur(4 if key == "hope" else 1)).point(lambda v: v * 3 // 5 if key == "hope" else v)
        picture.paste(layer.bright, mask=_upscaled(soft, picture.size))


def _add_smoke(picture: Image.Image, radius: float, rng: random.Random) -> None:
    """Клубы поверх цвета: аура перестаёт быть плоской заливкой."""
    size = (WIDTH, HEIGHT)
    area = Image.new("L", size, 0)
    ImageDraw.Draw(area).polygon(_contour(radius * 1.04, lambda _: 0.0), fill=255)
    random_state = random.getstate()
    random.seed(rng.getrandbits(32))
    cloud = Image.effect_noise((WIDTH // 28, HEIGHT // 28), 90).resize(size, Image.BICUBIC).filter(ImageFilter.GaussianBlur(10))
    random.setstate(random_state)
    smoke = ImageChops.multiply(cloud.point(lambda v: max(0, v - 118) * 3 // 2), area.filter(ImageFilter.GaussianBlur(20)))
    picture.paste(SMOKE, mask=_upscaled(smoke, picture.size))


def _silhouette() -> Image.Image:
    size = (WIDTH * SCALE, HEIGHT * SCALE)
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    left = [(96, 1120), (100, 950), (136, 842), (212, 786), (294, 752), (322, 722), (326, 680), (318, 600)]
    right = [(738 - x, y) for x, y in reversed(left)]
    draw.polygon(art.scaled(art.closed_curve([*left, *right])), fill=255)
    draw.ellipse(tuple(value * SCALE for value in (287, 412, 451, 636)), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(3))


def _paint_figure(picture: Image.Image, shown: list[str]) -> None:
    figure = _silhouette()
    halo = figure.filter(ImageFilter.GaussianBlur(14 * SCALE)).point(lambda v: v // 2)
    picture.paste((255, 236, 204), mask=halo)
    picture.paste(SILHOUETTE, mask=figure)

    draw = ImageDraw.Draw(picture)
    x, y, radius = 369 * SCALE, 484 * SCALE, 8 * SCALE
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=art.GOLD)

    label_font = art.font(25)
    for index, key in enumerate(shown):
        layer = LAYERS[key]
        line_y = (880 + index * 44 + (3 - len(shown)) * 22) * SCALE
        width = draw.textlength(layer.label, font=label_font)
        left = (369 * SCALE) - width / 2
        dot = 9 * SCALE
        draw.ellipse((left - 26 * SCALE - dot, line_y - dot, left - 26 * SCALE + dot, line_y + dot), fill=layer.bright)
        draw.text((left, line_y), layer.label, font=label_font, fill=layer.bright, anchor="lm")


def render(marked: Iterable[str] = ()) -> bytes:
    """Нарисовать ауру. ``marked`` — чувства из расклада, снаружи внутрь в постоянном порядке."""
    chosen = set(marked)
    if unknown := chosen - set(LAYERS):
        raise ValueError(f"неизвестные чувства: {sorted(unknown)}")
    rng = random.Random(11)
    image, picture, box = art.canvas(TITLE, rng)
    shown = [key for key in LAYERS if key in chosen]
    radii = RADII.get(len(shown), ())
    for key, radius in zip(shown, radii):
        _paint_layer(picture, key, radius, rng)
    if radii:
        _add_smoke(picture, radii[0], rng)
    _paint_figure(picture, shown)

    clip = Image.new("L", picture.size, 255)
    ImageDraw.Draw(clip).rectangle(box, fill=0)
    picture.paste(art.FRAME, mask=clip)
    return art.finish(image, picture, box)
