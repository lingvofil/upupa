"""Рисунок ладони для хиромантии: все четыре линии, а выпавшие в раскладе — с изъяном из текста.

Рисунок строится кодом, поэтому линия выглядит ровно так, как сказано в толковании:
сердце рваное, ум ломается посередине, судьба двоится, жизнь петляет. Telegram тут ни при чём:
модуль получает ключи сфер и отдаёт JPEG.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from games.guru import art
from games.guru.art import GOLD, SCALE, Point

TITLE = "ЛАДОНЬ КЛИЕНТА"

SKIN = (238, 214, 178)
SKIN_SHADOW = (168, 120, 92)
SKIN_LIGHT = (250, 234, 206)
OUTLINE = (70, 42, 34)
CREASE = (150, 104, 80)
INK = (96, 48, 38)
MARKED_INK = (150, 22, 34)
GLOW = (255, 196, 92)
LABEL_DIM = (172, 170, 190)


@dataclass(frozen=True)
class Finger:
    base: Point
    angle: float  # градусы от вертикали, плюс — вправо
    length: float
    base_radius: float
    tip_radius: float


FINGERS = (
    Finger((258, 494), -13, 150, 32, 25),  # мизинец
    Finger((331, 470), -4, 206, 36, 28),  # безымянный
    Finger((406, 460), 2, 232, 37, 29),  # средний
    Finger((482, 476), 9, 200, 36, 28),  # указательный
    Finger((530, 742), 33, 205, 58, 35),  # большой
)
PALM = (
    (224, 520), (216, 620), (222, 720), (240, 810), (272, 890), (286, 960), (284, 1080), (470, 1080), (470, 965),
    (492, 895), (545, 850), (592, 780), (586, 700), (548, 620), (532, 540), (518, 474), (440, 446), (350, 446), (268, 460),
)

HEART = ((222, 566), (292, 552), (370, 538), (446, 508), (506, 468))
HEAD = ((526, 640), (452, 642), (372, 658), (292, 702), (246, 748))
LIFE = ((530, 612), (472, 680), (442, 770), (440, 860), (460, 938))
FATE = ((392, 955), (388, 830), (395, 700), (404, 590), (408, 512))


def _normals(curve: Sequence[Point]) -> list[Point]:
    result = []
    for index in range(len(curve)):
        ax, ay = curve[max(index - 1, 0)]
        bx, by = curve[min(index + 1, len(curve) - 1)]
        length = math.hypot(bx - ax, by - ay) or 1.0
        result.append(((by - ay) / length, -(bx - ax) / length))
    return result


def _shifted(curve: Sequence[Point], offset: Callable[[float], float]) -> list[Point]:
    """Сдвинуть кривую вбок; ``offset`` получает долю пути от 0 до 1."""
    last = max(len(curve) - 1, 1)
    return [(x + nx * offset(i / last), y + ny * offset(i / last)) for i, ((x, y), (nx, ny)) in enumerate(zip(curve, _normals(curve)))]


def _slice(curve: Sequence[Point], start: float, end: float) -> list[Point]:
    last = len(curve) - 1
    return list(curve[round(start * last):round(end * last) + 1])


def _torn(curve: Sequence[Point], rng: random.Random) -> list[list[Point]]:
    """Рваная линия: куски со сдвигами и зазорами, а между ними торчат короткие волоски."""
    pieces: list[list[Point]] = []
    position = 0.0
    while position < 1.0:
        end = min(position + rng.uniform(0.11, 0.17), 1.0)
        jog = rng.uniform(-7, 7)
        pieces.append(_shifted(_slice(curve, position, end), lambda _: jog))
        if end < 1.0:
            x, y = curve[round(end * (len(curve) - 1))]
            pieces.append([(x, y + jog), (x + rng.uniform(-9, 9), y + jog + rng.choice((-1, 1)) * rng.uniform(13, 22))])
        position = end + rng.uniform(0.025, 0.05)
    return pieces


def _broken(curve: Sequence[Point]) -> list[list[Point]]:
    """Линия ума ломается посередине: половины сдвинуты, между ними зигзаг."""
    first = _slice(curve, 0.0, 0.46)
    second = _shifted(_slice(curve, 0.54, 1.0), lambda _: 20)
    a, b = first[-1], second[0]
    zigzag = [a, ((a[0] * 2 + b[0]) / 3 + 6, (a[1] * 2 + b[1]) / 3 + 15), ((a[0] + b[0] * 2) / 3 - 6, (a[1] + b[1] * 2) / 3 - 13), b]
    return [first, zigzag, second]


def _doubled(curve: Sequence[Point]) -> list[list[Point]]:
    """Линия судьбы двоится: снизу одна, выше развилка."""
    fork = 0.42
    spread = lambda t: 21 * max(0.0, (t - fork) / (1 - fork)) ** 0.8
    return [_slice(curve, 0.0, fork), _shifted(curve, spread)[round(fork * (len(curve) - 1)):], _shifted(curve, lambda t: -spread(t))[round(fork * (len(curve) - 1)):]]


def _looped(curve: Sequence[Point]) -> list[list[Point]]:
    """Линия жизни петляет: ровная дуга превращается в извилистую."""
    return [_shifted(curve, lambda t: 15 * math.sin(t * math.pi * 2 * 4.5))]


@dataclass(frozen=True)
class Line:
    points: tuple[Point, ...]
    label: str
    label_at: Point  # где стоит подпись
    leader: tuple[Point, Point]  # указатель от подписи к линии
    anchor: str = "la"


LINES = {
    "love": Line(HEART, "Линия сердца", (58, 592), ((200, 603), (229, 568))),
    "work": Line(HEAD, "Линия ума", (58, 762), ((154, 774), (252, 744))),
    "family": Line(LIFE, "Линия жизни", (684, 928), ((536, 941), (464, 936)), "ra"),
    "money": Line(FATE, "Линия судьбы", (58, 985), ((210, 996), (390, 940))),
}

# Второстепенные линии: без подписей и изъянов, но ладонь без них выглядит голой.
MINOR_LINES = (
    ((252, 556), (292, 660), (330, 770), (352, 860)),  # печени
    ((345, 548), (350, 500), (352, 470)),  # Солнца
    ((292, 504), (330, 524), (392, 520), (452, 490)),  # пояс Венеры
    ((300, 985), (372, 1000), (458, 985)),  # браслеты
    ((302, 1010), (372, 1024), (456, 1010)),
)


def _marked(key: str, curve: list[Point], rng: random.Random) -> list[list[Point]]:
    if key == "love":
        return _torn(curve, rng)
    if key == "work":
        return _broken(curve)
    if key == "money":
        return _doubled(curve)
    return _looped(curve)


def _finger_shape(finger: Finger) -> tuple[list[Point], Point, float]:
    angle = math.radians(finger.angle)
    ax, ay = math.sin(angle), -math.cos(angle)
    px, py = math.cos(angle), math.sin(angle)
    bx, by = finger.base
    tip = (bx + ax * finger.length, by + ay * finger.length)
    outline = [(bx - px * finger.base_radius, by - py * finger.base_radius), (tip[0] - px * finger.tip_radius, tip[1] - py * finger.tip_radius)]
    for step in range(1, 16):
        turn = math.pi * step / 16
        outline.append((tip[0] - math.cos(turn) * px * finger.tip_radius + math.sin(turn) * ax * finger.tip_radius,
                        tip[1] - math.cos(turn) * py * finger.tip_radius + math.sin(turn) * ay * finger.tip_radius))
    outline += [(tip[0] + px * finger.tip_radius, tip[1] + py * finger.tip_radius), (bx + px * finger.base_radius, by + py * finger.base_radius)]
    return outline, tip, finger.base_radius


def _hand_mask(size: tuple[int, int]) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(art.scaled(art.closed_curve(PALM)), fill=255)
    for finger in FINGERS:
        outline, _, radius = _finger_shape(finger)
        draw.polygon(art.scaled(outline), fill=255)
        bx, by = finger.base
        scaled_radius = radius * SCALE
        draw.ellipse((bx * SCALE - scaled_radius, by * SCALE - scaled_radius, bx * SCALE + scaled_radius, by * SCALE + scaled_radius), fill=255)
    return mask


def _creases(draw: ImageDraw.ImageDraw) -> None:
    """Сгибы фаланг: короткие дуги поперёк пальцев."""
    for finger in FINGERS:
        angle = math.radians(finger.angle)
        ax, ay = math.sin(angle), -math.cos(angle)
        px, py = math.cos(angle), math.sin(angle)
        marks = (0.30, 0.64) if finger is not FINGERS[-1] else (0.52,)
        for share in marks:
            cx = finger.base[0] + ax * finger.length * share
            cy = finger.base[1] + ay * finger.length * share
            half = (finger.base_radius + (finger.tip_radius - finger.base_radius) * share) * 0.72
            arc = art.catmull([(cx - px * half, cy - py * half), (cx - ax * 4, cy - ay * 4), (cx + px * half, cy + py * half)], 10)
            draw.line(art.scaled(arc), fill=CREASE, width=2 * SCALE, joint="curve")
            shifted = [(x + ax * 7, y + ay * 7) for x, y in arc]
            draw.line(art.scaled(shifted), fill=CREASE, width=1 * SCALE, joint="curve")


def _stroke(draw: ImageDraw.ImageDraw, pieces: Iterable[Sequence[Point]], fill: int | tuple[int, int, int], width: int) -> None:
    for piece in pieces:
        scaled = art.scaled(piece)
        draw.line(scaled, fill=fill, width=width, joint="curve")
        radius = width / 2
        for x, y in (scaled[0], scaled[-1]):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _label(draw: ImageDraw.ImageDraw, line: Line, marked: bool) -> None:
    color = GOLD if marked else LABEL_DIM
    x, y = line.label_at
    draw.text((x * SCALE, y * SCALE), line.label, font=art.font(22), fill=color, anchor=line.anchor)
    draw.line(art.scaled(line.leader), fill=color, width=SCALE)
    ax, ay = line.leader[1]
    dot = 3.5 * SCALE
    draw.ellipse((ax * SCALE - dot, ay * SCALE - dot, ax * SCALE + dot, ay * SCALE + dot), fill=color)


def render(marked: Iterable[str] = ()) -> bytes:
    """Нарисовать ладонь. ``marked`` — сферы из расклада: их линии получают изъян и подсветку."""
    chosen = set(marked)
    if unknown := chosen - set(LINES):
        raise ValueError(f"неизвестные линии: {sorted(unknown)}")
    rng = random.Random(7)
    image, picture, box = art.canvas(TITLE, rng)
    size = image.size

    hand = _hand_mask(size)
    clip = Image.new("L", size, 0)
    ImageDraw.Draw(clip).rectangle(box, fill=255)
    hand = ImageChops.multiply(hand, clip)

    rim = hand.filter(ImageFilter.GaussianBlur(2.5 * SCALE)).point(lambda v: 255 if v > 26 else 0)
    picture.paste(OUTLINE, mask=ImageChops.multiply(rim, clip))
    picture.paste(SKIN, mask=hand)

    inner = ImageChops.multiply(hand, ImageChops.invert(hand.filter(ImageFilter.GaussianBlur(17 * SCALE)))).point(lambda v: min(255, v * 2))
    picture.paste(SKIN_SHADOW, mask=inner)
    light = Image.new("L", size, 0)
    ImageDraw.Draw(light).ellipse((452 * SCALE, 650 * SCALE, 572 * SCALE, 850 * SCALE), fill=120)
    ImageDraw.Draw(light).ellipse((238 * SCALE, 610 * SCALE, 340 * SCALE, 860 * SCALE), fill=70)
    picture.paste(SKIN_LIGHT, mask=ImageChops.multiply(light.filter(ImageFilter.GaussianBlur(32 * SCALE)), hand))

    draw = ImageDraw.Draw(picture)
    _creases(draw)
    for minor in MINOR_LINES:
        draw.line(art.scaled(art.catmull(minor, 12)), fill=CREASE, width=2 * SCALE, joint="curve")

    glow = Image.new("L", size, 0)
    glow_draw = ImageDraw.Draw(glow)
    shapes: dict[str, list[list[Point]]] = {}
    for key, line in LINES.items():
        curve = art.catmull(line.points)
        shapes[key] = _marked(key, curve, rng) if key in chosen else [curve]
        if key in chosen:
            _stroke(glow_draw, shapes[key], 255, 11 * SCALE)
    picture.paste(GLOW, mask=ImageChops.multiply(glow.filter(ImageFilter.GaussianBlur(6 * SCALE)), hand))

    for key, pieces in shapes.items():
        _stroke(draw, pieces, MARKED_INK if key in chosen else INK, (5 if key in chosen else 4) * SCALE)
    for key, line in LINES.items():
        _label(draw, line, key in chosen)

    return art.finish(image, picture, box)
