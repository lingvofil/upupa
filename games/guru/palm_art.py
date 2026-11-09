"""Рисунок ладони для хиромантии: все четыре линии, а выпавшие в раскладе — с изъяном из текста.

Рисунок строится кодом, поэтому линия выглядит ровно так, как сказано в толковании:
сердце рваное, ум ломается посередине, судьба двоится, жизнь петляет. Telegram тут ни при чём:
модуль получает ключи сфер и отдаёт JPEG.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from core.paths import PROJECT_ROOT

Point = tuple[float, float]

WIDTH, HEIGHT = 738, 1100
SCALE = 2  # рисуем крупнее и уменьшаем: так края гладкие
TITLE = "ЛАДОНЬ КЛИЕНТА"
FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "BerlinSansFBCyrillic-Regular.ttf"

PARCHMENT = (214, 190, 148)
FRAME = (20, 17, 16)
GOLD = (222, 178, 96)
GOLD_DIM = (146, 116, 66)
SKY_TOP = (22, 30, 66)
SKY_BOTTOM = (70, 48, 74)
SKIN = (238, 214, 178)
SKIN_SHADOW = (168, 120, 92)
SKIN_LIGHT = (250, 234, 206)
OUTLINE = (70, 42, 34)
CREASE = (150, 104, 80)
INK = (96, 48, 38)
MARKED_INK = (150, 22, 34)
GLOW = (255, 196, 92)
LABEL_DIM = (172, 170, 190)

PICTURE = (48, 100, 690, 1040)


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


def _catmull(points: Sequence[Point], steps: int = 24) -> list[Point]:
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


def _closed_curve(points: Sequence[Point]) -> list[Point]:
    knots = [points[-1], *points, points[0], points[1]]
    curve: list[Point] = []
    for i in range(1, len(knots) - 2):
        curve += _catmull([knots[i - 1], knots[i], knots[i + 1], knots[i + 2]], 16)[16:32]
    return curve


def _scaled(points: Iterable[Point]) -> list[Point]:
    return [(x * SCALE, y * SCALE) for x, y in points]


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


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size * SCALE)


def _sky(size: tuple[int, int], rng: random.Random) -> Image.Image:
    width, height = size
    sky = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(height - 1, 1)
        sky.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(SKY_TOP, SKY_BOTTOM)))
    sky = sky.resize(size)
    draw = ImageDraw.Draw(sky)
    for _ in range(220):
        x, y = rng.uniform(0, width), rng.uniform(0, height)
        radius = rng.choice((1, 1, 1, 2, 3)) * SCALE * 0.6
        light = rng.randint(150, 255)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(light, light, min(255, light + 20)))
    return sky


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
    draw.polygon(_scaled(_closed_curve(PALM)), fill=255)
    for finger in FINGERS:
        outline, _, radius = _finger_shape(finger)
        draw.polygon(_scaled(outline), fill=255)
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
            arc = _catmull([(cx - px * half, cy - py * half), (cx - ax * 4, cy - ay * 4), (cx + px * half, cy + py * half)], 10)
            draw.line(_scaled(arc), fill=CREASE, width=2 * SCALE, joint="curve")
            shifted = [(x + ax * 7, y + ay * 7) for x, y in arc]
            draw.line(_scaled(shifted), fill=CREASE, width=1 * SCALE, joint="curve")


def _stroke(draw: ImageDraw.ImageDraw, pieces: Iterable[Sequence[Point]], fill: int | tuple[int, int, int], width: int) -> None:
    for piece in pieces:
        scaled = _scaled(piece)
        draw.line(scaled, fill=fill, width=width, joint="curve")
        radius = width / 2
        for x, y in (scaled[0], scaled[-1]):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _label(draw: ImageDraw.ImageDraw, line: Line, marked: bool) -> None:
    color = GOLD if marked else LABEL_DIM
    x, y = line.label_at
    draw.text((x * SCALE, y * SCALE), line.label, font=_font(22), fill=color, anchor=line.anchor)
    draw.line(_scaled(line.leader), fill=color, width=SCALE)
    ax, ay = line.leader[1]
    dot = 3.5 * SCALE
    draw.ellipse((ax * SCALE - dot, ay * SCALE - dot, ax * SCALE + dot, ay * SCALE + dot), fill=color)


def _frame(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    draw.rectangle((18 * SCALE, 18 * SCALE, width - 18 * SCALE, height - 18 * SCALE), fill=FRAME)
    for inset, color, line in ((26, GOLD_DIM, 1), (32, GOLD, 2)):
        draw.rectangle((inset * SCALE, inset * SCALE, width - inset * SCALE, height - inset * SCALE), outline=color, width=line * SCALE)
    draw.text((width / 2, 66 * SCALE), TITLE, font=_font(38), fill=GOLD, anchor="mm")
    for x in (64, WIDTH - 64):
        cx, cy, radius = x * SCALE, 66 * SCALE, 16 * SCALE
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=GOLD, width=2 * SCALE)
        draw.polygon([(cx + math.sin(math.radians(a * 144)) * radius, cy - math.cos(math.radians(a * 144)) * radius) for a in range(5)], outline=GOLD)


def render(marked: Iterable[str] = ()) -> bytes:
    """Нарисовать ладонь. ``marked`` — сферы из расклада: их линии получают изъян и подсветку."""
    chosen = set(marked)
    if unknown := chosen - set(LINES):
        raise ValueError(f"неизвестные линии: {sorted(unknown)}")
    size = (WIDTH * SCALE, HEIGHT * SCALE)
    rng = random.Random(7)

    image = Image.new("RGB", size, PARCHMENT)
    _frame(image)

    left, top, right, bottom = PICTURE
    box = (left * SCALE, top * SCALE, right * SCALE, bottom * SCALE)
    picture_size = (box[2] - box[0], box[3] - box[1])
    picture = Image.new("RGB", size, FRAME)
    picture.paste(_sky(picture_size, rng), box[:2])

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
        draw.line(_scaled(_catmull(minor, 12)), fill=CREASE, width=2 * SCALE, joint="curve")

    glow = Image.new("L", size, 0)
    glow_draw = ImageDraw.Draw(glow)
    shapes: dict[str, list[list[Point]]] = {}
    for key, line in LINES.items():
        curve = _catmull(line.points)
        shapes[key] = _marked(key, curve, rng) if key in chosen else [curve]
        if key in chosen:
            _stroke(glow_draw, shapes[key], 255, 11 * SCALE)
    picture.paste(GLOW, mask=ImageChops.multiply(glow.filter(ImageFilter.GaussianBlur(6 * SCALE)), hand))

    for key, pieces in shapes.items():
        _stroke(draw, pieces, MARKED_INK if key in chosen else INK, (5 if key in chosen else 4) * SCALE)
    for key, line in LINES.items():
        _label(draw, line, key in chosen)

    grain = Image.effect_noise((WIDTH // 2, HEIGHT // 2), 26).resize(size, Image.BILINEAR).point(lambda v: max(0, (v - 128) // 3))
    picture.paste((30, 22, 18), mask=grain)
    image.paste(picture.crop(box), box[:2])
    frame = ImageDraw.Draw(image)
    frame.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), outline=GOLD, width=2 * SCALE)

    output = io.BytesIO()
    image.resize((WIDTH, HEIGHT), Image.LANCZOS).save(output, "JPEG", quality=86, optimize=True, progressive=True)
    return output.getvalue()
