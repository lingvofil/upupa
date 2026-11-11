"""Рисунок ладони для хиромантии: все четыре линии, а выпавшие в раскладе — с изъяном из текста.

Рисунок строится кодом, поэтому линия выглядит ровно так, как сказано в толковании:
сердце рваное, ум ломается посередине, судьба двоится, жизнь петляет. Telegram тут ни при чём:
модуль получает ключи сфер и отдаёт JPEG.

Рук несколько типов (``HANDS``): в живом приёме клиент выбирает ту, что похожа на его собственную.
``render_study`` рисует ладонь для осмотра по областям: у неё свои приметы, а подсвечено то,
что мадам уже осмотрела.
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


def _forked(curve: Sequence[Point]) -> list[list[Point]]:
    """Линия в конце расходится на две ветви."""
    fork = 0.7
    start = round(fork * (len(curve) - 1))
    spread = lambda t: 26 * max(0.0, (t - fork) / (1 - fork)) ** 0.9
    return [list(curve), _shifted(curve, spread)[start:]]


# --- тип руки ---------------------------------------------------------------

@dataclass(frozen=True)
class Hand:
    """Пропорции руки: в живом приёме клиент выбирает ту, что похожа на его собственную."""

    width: float = 1.0  # ширина ладони, доля от обычной
    fingers: float = 1.0  # длина пальцев
    girth: float = 1.0  # толщина пальцев
    thumb: float = 0.0  # на сколько градусов отставлен большой палец


PLAIN_HAND = "plain"
HANDS = {
    PLAIN_HAND: Hand(),
    "earth": Hand(1.07, 0.82, 1.10),  # широкая ладонь, короткие пальцы
    "air": Hand(1.05, 1.12, 0.98),  # широкая ладонь, длинные пальцы
    "fire": Hand(0.92, 0.84, 1.00),  # узкая ладонь, короткие пальцы
    "water": Hand(0.90, 1.14, 0.86),  # узкая ладонь, длинные тонкие пальцы
}
AXIS = 400  # вертикаль, от которой ладонь становится шире или уже


def _fit(hand: Hand, points: Iterable[Point]) -> tuple[Point, ...]:
    return tuple((AXIS + (x - AXIS) * hand.width, y) for x, y in points)


def _fingers(hand: Hand) -> tuple[Finger, ...]:
    result = []
    for index, finger in enumerate(FINGERS):
        thumb = index == len(FINGERS) - 1
        result.append(Finger(_fit(hand, [finger.base])[0], finger.angle + (hand.thumb if thumb else 0.0),
                             finger.length * hand.fingers, finger.base_radius * hand.girth, finger.tip_radius * hand.girth))
    return tuple(result)


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


def _hand_mask(size: tuple[int, int], hand: Hand) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(art.scaled(art.closed_curve(_fit(hand, PALM))), fill=255)
    for finger in _fingers(hand):
        outline, _, radius = _finger_shape(finger)
        draw.polygon(art.scaled(outline), fill=255)
        bx, by = finger.base
        scaled_radius = radius * SCALE
        draw.ellipse((bx * SCALE - scaled_radius, by * SCALE - scaled_radius, bx * SCALE + scaled_radius, by * SCALE + scaled_radius), fill=255)
    return mask


def _creases(draw: ImageDraw.ImageDraw, hand: Hand) -> None:
    """Сгибы фаланг: короткие дуги поперёк пальцев."""
    fingers = _fingers(hand)
    for finger in fingers:
        angle = math.radians(finger.angle)
        ax, ay = math.sin(angle), -math.cos(angle)
        px, py = math.cos(angle), math.sin(angle)
        marks = (0.30, 0.64) if finger is not fingers[-1] else (0.52,)
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


def _label(draw: ImageDraw.ImageDraw, line: Line, marked: bool, hand: Hand) -> None:
    color = GOLD if marked else LABEL_DIM
    x, y = line.label_at
    draw.text((x * SCALE, y * SCALE), line.label, font=art.font(22), fill=color, anchor=line.anchor)
    leader = (line.leader[0], _fit(hand, [line.leader[1]])[0])
    draw.line(art.scaled(leader), fill=color, width=SCALE)
    ax, ay = leader[1]
    dot = 3.5 * SCALE
    draw.ellipse((ax * SCALE - dot, ay * SCALE - dot, ax * SCALE + dot, ay * SCALE + dot), fill=color)


Shape = tuple[list[list[Point]], bool]  # куски линии и подсвечена ли она


def _paint(hand: Hand, shapes: Iterable[Shape], labels: Iterable[tuple[Line, bool]], rng: random.Random,
           spots: Iterable[tuple[Point, float]] = ()) -> bytes:
    """Рука с линиями и подписями. ``spots`` — подсвеченные места вне линий: (центр, радиус)."""
    image, picture, box = art.canvas(TITLE, rng)
    size = image.size

    mask = _hand_mask(size, hand)
    clip = Image.new("L", size, 0)
    ImageDraw.Draw(clip).rectangle(box, fill=255)
    mask = ImageChops.multiply(mask, clip)

    rim = mask.filter(ImageFilter.GaussianBlur(2.5 * SCALE)).point(lambda v: 255 if v > 26 else 0)
    picture.paste(OUTLINE, mask=ImageChops.multiply(rim, clip))
    picture.paste(SKIN, mask=mask)

    inner = ImageChops.multiply(mask, ImageChops.invert(mask.filter(ImageFilter.GaussianBlur(17 * SCALE)))).point(lambda v: min(255, v * 2))
    picture.paste(SKIN_SHADOW, mask=inner)
    light = Image.new("L", size, 0)
    ImageDraw.Draw(light).ellipse((452 * SCALE, 650 * SCALE, 572 * SCALE, 850 * SCALE), fill=120)
    ImageDraw.Draw(light).ellipse((238 * SCALE, 610 * SCALE, 340 * SCALE, 860 * SCALE), fill=70)
    picture.paste(SKIN_LIGHT, mask=ImageChops.multiply(light.filter(ImageFilter.GaussianBlur(32 * SCALE)), mask))

    draw = ImageDraw.Draw(picture)
    _creases(draw, hand)
    for minor in MINOR_LINES:
        draw.line(art.scaled(art.catmull(_fit(hand, minor), 12)), fill=CREASE, width=2 * SCALE, joint="curve")

    shapes = list(shapes)
    glow = Image.new("L", size, 0)
    glow_draw = ImageDraw.Draw(glow)
    for pieces, marked in shapes:
        if marked:
            _stroke(glow_draw, pieces, 255, 11 * SCALE)
    for (x, y), radius in spots:
        glow_draw.ellipse(((x - radius) * SCALE, (y - radius) * SCALE, (x + radius) * SCALE, (y + radius) * SCALE), fill=200)
    picture.paste(GLOW, mask=ImageChops.multiply(glow.filter(ImageFilter.GaussianBlur(6 * SCALE)), mask))

    for pieces, marked in shapes:
        _stroke(draw, pieces, MARKED_INK if marked else INK, (5 if marked else 4) * SCALE)
    for line, marked in labels:
        _label(draw, line, marked, hand)
    return art.finish(image, picture, box)


def render(marked: Iterable[str] = (), hand: str = PLAIN_HAND) -> bytes:
    """Нарисовать ладонь. ``marked`` — сферы из расклада: их линии получают изъян и подсветку. ``hand`` — тип руки."""
    chosen = set(marked)
    if unknown := chosen - set(LINES):
        raise ValueError(f"неизвестные линии: {sorted(unknown)}")
    if hand not in HANDS:
        raise ValueError(f"неизвестный тип руки: {hand}")
    shape = HANDS[hand]
    rng = random.Random(7)
    shapes: list[Shape] = []
    for key, line in LINES.items():
        curve = art.catmull(_fit(shape, line.points))
        shapes.append((_marked(key, curve, rng) if key in chosen else [curve], key in chosen))
    return _paint(shape, shapes, [(line, key in chosen) for key, line in LINES.items()], rng)


# --- ладонь для осмотра по областям -----------------------------------------

# У этой руки свои приметы: линия сердца в конце раздваивается, линия ума идёт рядом с ней,
# но отдельно, а большой палец заметно отставлен. Мадам выбирает, какие области осмотреть.
STUDY_HAND = Hand(thumb=13.0)
STUDY_HEAD = ((522, 600), (450, 604), (372, 612), (298, 632), (246, 660))
STUDY_GAP = ((556, 668), 46.0)  # промежуток между большим пальцем и ладонью
AREAS = {
    "heart": Line(HEART, "Сердце", (58, 592), ((132, 603), (229, 568))),
    "head": Line(STUDY_HEAD, "Голова", (58, 722), ((130, 733), (252, 660))),
    "thumb": Line((), "Большой палец", (684, 905), ((560, 893), (560, 706)), "ra"),
}


def render_study(inspected: Iterable[str] = ()) -> bytes:
    """Ладонь, которую осматривают по областям. ``inspected`` — уже осмотренные: они подсвечены."""
    seen = set(inspected)
    if unknown := seen - set(AREAS):
        raise ValueError(f"неизвестные области: {sorted(unknown)}")
    rng = random.Random(7)
    shapes: list[Shape] = [
        (_forked(art.catmull(HEART)), "heart" in seen),
        ([art.catmull(STUDY_HEAD)], "head" in seen),
        ([art.catmull(LIFE)], False),
        ([art.catmull(FATE)], False),
    ]
    spots = [STUDY_GAP] if "thumb" in seen else []
    return _paint(STUDY_HAND, shapes, [(area, key in seen) for key, area in AREAS.items()], rng, spots)
