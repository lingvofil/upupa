"""Рисунок салона: комната мадам, купленные вещи на своих местах и клиент за столом.

Вид — глазами мадам: стол, напротив кресло клиента, за ним стена. Каждая покупка из лавки
(``games.guru.salon``) появляется в комнате: реквизит лежит на столе, реклама висит на стене,
уют стоит вокруг. Чем больше куплено, тем богаче стены: от каморки за гаражами до храма.
Подарки клиентов стоят на полке. Модуль получает ключи и отдаёт JPEG.
"""

from __future__ import annotations

import math
import random
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from games.guru import art, salon
from games.guru.art import GOLD, GOLD_DIM, HEIGHT, SCALE, WIDTH, Point

Color = tuple[int, int, int]
Light = tuple[float, float, float, Color]  # где, радиус, цвет

LEFT, TOP, RIGHT, BOTTOM = art.PICTURE
CENTER = 369
FLOOR = 772  # где стена встречается с полом
SHELF = 232  # доска полки с подарками
SHELF_LEFT, SHELF_RIGHT = 62, 676
GIFT_ZOOM = 1.3  # подарки крупнее, чем нарисованы: иначе на полке их не разглядеть
GUEST = salon.GUEST  # клиент без примет: живой человек из чата

BRICK = (124, 62, 48)
MORTAR = (86, 78, 74)
WOOD = (96, 62, 42)
WOOD_DARK = (58, 38, 28)
CLOTH = (128, 34, 52)
CLOTH_DARK = (84, 20, 36)
PAPER = (236, 224, 196)
INK = (58, 44, 40)
BODY = (26, 20, 36)
WAX = (238, 226, 196)
FLAME = (255, 196, 84)
NEON = (255, 92, 168)
VELVET = (44, 104, 82)
VELVET_LIGHT = (66, 134, 106)
SKIN = (226, 184, 152)

# Стены по званию салона: (цвет, узор). Порог — число покупок, как в ``salon.RANKS``.
WALLS: tuple[tuple[int, Color, str], ...] = (
    (12, (132, 36, 48), "diamonds"),
    (8, (44, 56, 122), "stars"),
    (4, (112, 58, 92), "stripes"),
    (1, (70, 116, 110), "panel"),
    (0, (112, 106, 104), "bricks"),
)


class _Pen:
    """Перо в координатах готовой картинки: само переводит их в крупный холст."""

    def __init__(self, image: Image.Image) -> None:
        self.image = image
        self.draw = ImageDraw.Draw(image)

    @staticmethod
    def _xy(values: Sequence[float]) -> list[float]:
        return [value * SCALE for value in values]

    @staticmethod
    def _w(width: float) -> int:
        return max(1, round(width * SCALE))

    def box(self, box: Sequence[float], fill=None, outline=None, width: float = 1, radius: float = 0) -> None:
        if radius:
            self.draw.rounded_rectangle(self._xy(box), radius * SCALE, fill=fill, outline=outline, width=self._w(width))
        else:
            self.draw.rectangle(self._xy(box), fill=fill, outline=outline, width=self._w(width))

    def oval(self, box: Sequence[float], fill=None, outline=None, width: float = 1) -> None:
        self.draw.ellipse(self._xy(box), fill=fill, outline=outline, width=self._w(width))

    def disc(self, x: float, y: float, radius: float, fill=None, outline=None, width: float = 1) -> None:
        self.oval((x - radius, y - radius, x + radius, y + radius), fill, outline, width)

    def poly(self, points: Iterable[Point], fill=None, outline=None) -> None:
        self.draw.polygon(art.scaled(points), fill=fill, outline=outline)

    def line(self, points: Iterable[Point], fill, width: float = 1) -> None:
        self.draw.line(art.scaled(points), fill=fill, width=self._w(width), joint="curve")

    def arc(self, box: Sequence[float], start: float, end: float, fill, width: float = 1) -> None:
        self.draw.arc(self._xy(box), start, end, fill=fill, width=self._w(width))

    def chord(self, box: Sequence[float], start: float, end: float, fill) -> None:
        self.draw.chord(self._xy(box), start, end, fill=fill)

    def text(self, x: float, y: float, text: str, size: int, fill, anchor: str = "mm", stroke: float = 0, stroke_fill=None) -> None:
        self.draw.text((x * SCALE, y * SCALE), text, font=art.font(size), fill=fill, anchor=anchor,
                       stroke_width=round(stroke * SCALE), stroke_fill=stroke_fill)


class _Zoom:
    """Перо, которое рисует крупнее вокруг точки опоры: подарки на полке видны без лупы."""

    def __init__(self, pen: _Pen, origin: Point, factor: float) -> None:
        self.pen = pen
        self.origin = origin
        self.factor = factor

    def _x(self, x: float) -> float:
        return self.origin[0] + (x - self.origin[0]) * self.factor

    def _y(self, y: float) -> float:
        return self.origin[1] + (y - self.origin[1]) * self.factor

    def _box(self, box: Sequence[float]) -> tuple[float, float, float, float]:
        return self._x(box[0]), self._y(box[1]), self._x(box[2]), self._y(box[3])

    def _points(self, points: Iterable[Point]) -> list[Point]:
        return [(self._x(x), self._y(y)) for x, y in points]

    def box(self, box: Sequence[float], fill=None, outline=None, width: float = 1, radius: float = 0) -> None:
        self.pen.box(self._box(box), fill, outline, width * self.factor, radius * self.factor)

    def oval(self, box: Sequence[float], fill=None, outline=None, width: float = 1) -> None:
        self.pen.oval(self._box(box), fill, outline, width * self.factor)

    def disc(self, x: float, y: float, radius: float, fill=None, outline=None, width: float = 1) -> None:
        self.pen.disc(self._x(x), self._y(y), radius * self.factor, fill, outline, width * self.factor)

    def poly(self, points: Iterable[Point], fill=None, outline=None) -> None:
        self.pen.poly(self._points(points), fill, outline)

    def line(self, points: Iterable[Point], fill, width: float = 1) -> None:
        self.pen.line(self._points(points), fill, width * self.factor)

    def arc(self, box: Sequence[float], start: float, end: float, fill, width: float = 1) -> None:
        self.pen.arc(self._box(box), start, end, fill, width * self.factor)

    def text(self, x: float, y: float, text: str, size: int, fill, anchor: str = "mm") -> None:
        self.pen.text(self._x(x), self._y(y), text, round(size * self.factor), fill, anchor)


class _Scene:
    """Холст, свет и то, что рисуется поверх теней: огонь, неон, экраны."""

    def __init__(self, picture: Image.Image) -> None:
        self.picture = picture
        self.pen = _Pen(picture)
        self.lights: list[Light] = []
        self.glowing: list[Callable[[_Pen], None]] = []

    def inside(self, shape: Callable[[_Pen], None], paint: Callable[[_Pen], None]) -> None:
        """Нарисовать ``paint`` только там, где ``shape`` закрасил маску."""
        mask = Image.new("L", self.picture.size, 0)
        shape(_Pen(mask))
        layer = self.picture.copy()
        paint(_Pen(layer))
        self.picture.paste(layer, mask=mask)
        self.pen = _Pen(self.picture)


def _turned(points: Iterable[Point], center: Point, degrees: float) -> list[Point]:
    sin, cos = math.sin(math.radians(degrees)), math.cos(math.radians(degrees))
    cx, cy = center
    return [(cx + (x - cx) * cos - (y - cy) * sin, cy + (x - cx) * sin + (y - cy) * cos) for x, y in points]


def _slab(center: Point, width: float, height: float, degrees: float) -> list[Point]:
    """Повёрнутый прямоугольник: карта, книга, тетрадь."""
    cx, cy = center
    corners = [(cx - width / 2, cy - height / 2), (cx + width / 2, cy - height / 2),
               (cx + width / 2, cy + height / 2), (cx - width / 2, cy + height / 2)]
    return _turned(corners, center, degrees)


def _star(x: float, y: float, radius: float, points: int = 5, inner: float = 0.42) -> list[Point]:
    return [(x + math.sin(math.pi * step / points) * radius * (1 if step % 2 == 0 else inner),
             y - math.cos(math.pi * step / points) * radius * (1 if step % 2 == 0 else inner))
            for step in range(points * 2)]


def _mix(color: Color, other: Color, share: float) -> Color:
    return tuple(round(a + (b - a) * share) for a, b in zip(color, other))


def _tier(count: int) -> tuple[Color, str]:
    return next((color, pattern) for threshold, color, pattern in WALLS if count >= threshold)


# --- комната ----------------------------------------------------------------

def _wall(scene: _Scene, count: int, rng: random.Random) -> None:
    pen = scene.pen
    color, pattern = _tier(count)
    pen.box((LEFT, TOP, RIGHT, FLOOR), fill=color)
    if pattern == "bricks":
        # Штукатурка отвалилась кусками, под ней кирпич.
        def bricks(layer: _Pen) -> None:
            layer.box((LEFT, TOP, RIGHT, FLOOR), fill=BRICK)
            for row, y in enumerate(range(TOP, FLOOR, 22)):
                layer.line([(LEFT, y), (RIGHT, y)], MORTAR, 2)
                for x in range(LEFT - (26 if row % 2 else 0), RIGHT, 52):
                    layer.line([(x, y), (x, y + 22)], MORTAR, 2)

        def holes(mask: _Pen) -> None:
            for cx, cy, wide, tall in ((150, 590, 96, 70), (560, 392, 92, 64), (300, 170, 70, 30), (640, 640, 60, 80)):
                blob = [(cx + math.cos(turn) * wide * rng.uniform(0.7, 1.1), cy + math.sin(turn) * tall * rng.uniform(0.7, 1.1))
                        for turn in (2 * math.pi * step / 9 for step in range(9))]
                mask.poly(art.closed_curve(blob), fill=255)

        scene.inside(holes, bricks)
        pen = scene.pen
        for _ in range(7):  # трещины
            x, y = rng.uniform(LEFT + 30, RIGHT - 30), rng.uniform(TOP + 40, FLOOR - 200)
            crack = [(x, y)]
            for _ in range(5):
                crack.append((crack[-1][0] + rng.uniform(-22, 22), crack[-1][1] + rng.uniform(14, 34)))
            pen.line(crack, _mix(color, (0, 0, 0), 0.45), 1.2)
    elif pattern == "panel":
        # Подвал: низ выкрашен масляной краской, верх побелён.
        pen.box((LEFT, TOP, RIGHT, 520), fill=(190, 184, 166))
        pen.line([(LEFT, 520), (RIGHT, 520)], _mix(color, (0, 0, 0), 0.4), 5)
        for _ in range(9):
            x, y = rng.uniform(LEFT, RIGHT), rng.uniform(TOP, 500)
            pen.oval((x - rng.uniform(20, 60), y - 8, x + rng.uniform(20, 60), y + rng.uniform(8, 20)), fill=(176, 170, 152))
    elif pattern == "stripes":
        light = _mix(color, (255, 255, 255), 0.1)
        for x in range(LEFT, RIGHT, 44):
            pen.box((x, TOP, x + 22, FLOOR), fill=light)
            pen.line([(x, TOP), (x, FLOOR)], _mix(color, GOLD, 0.35), 1)
    elif pattern == "stars":
        for row, y in enumerate(range(TOP + 30, FLOOR - 10, 58)):
            for x in range(LEFT + (30 if row % 2 else 59), RIGHT, 58):
                pen.poly(_star(x, y, 7, points=4, inner=0.3), fill=_mix(color, GOLD, 0.55))
    else:
        line = _mix(color, GOLD, 0.45)
        for offset in range(-700, 700, 66):
            pen.line([(LEFT + offset, TOP), (LEFT + offset + 672, FLOOR)], line, 1.4)
            pen.line([(RIGHT - offset, TOP), (RIGHT - offset - 672, FLOOR)], line, 1.4)
        for row, y in enumerate(range(TOP + 16, FLOOR, 33)):
            for x in range(LEFT + (0 if row % 2 else 33), RIGHT + 1, 66):
                pen.disc(x, y, 2.6, fill=GOLD_DIM)

    if pattern in ("bricks", "panel"):
        # Трубы под потолком: салон начинался не с парадной.
        pen.box((LEFT, 112, RIGHT, 128), fill=(92, 96, 104))
        pen.line([(LEFT, 115), (RIGHT, 115)], (140, 146, 156), 2)
        for x in (150, 430, 610):
            pen.box((x, 108, x + 12, 132), fill=(70, 74, 82))
    else:
        pen.box((LEFT, TOP, RIGHT, 116), fill=_mix(color, (0, 0, 0), 0.45))
        pen.line([(LEFT, 116), (RIGHT, 116)], GOLD_DIM, 2.5)

    pen.box((LEFT, FLOOR, RIGHT, BOTTOM), fill=(70, 48, 38))
    for x in range(LEFT, RIGHT, 74):
        pen.line([(x, FLOOR), (x - 30, BOTTOM)], (48, 32, 26), 2)
    pen.box((LEFT, FLOOR - 12, RIGHT, FLOOR), fill=_mix(color, (0, 0, 0), 0.6))


def _window(scene: _Scene, sky: Image.Image) -> None:
    pen = scene.pen
    box = (84, 262, 236, 462)
    scene.picture.paste(sky, (box[0] * SCALE, box[1] * SCALE))
    pen.disc(186, 318, 24, fill=(246, 238, 204))
    pen.disc(196, 310, 21, fill=art.SKY_TOP)
    pen.box(box, outline=WOOD_DARK, width=9)
    pen.line([(160, 262), (160, 462)], WOOD_DARK, 6)
    pen.line([(84, 350), (236, 350)], WOOD_DARK, 6)
    pen.box((74, 460, 246, 474), fill=WOOD)
    scene.lights.append((160, 350, 110, (70, 90, 160)))


def _neon(scene: _Scene) -> None:
    """Вывеска «МАГИЯ». Буква Г уже мигает."""
    scene.pen.box((252, 258, 486, 342), fill=(28, 22, 34), outline=(60, 50, 66), width=3, radius=10)
    for x in (270, 468):
        scene.pen.line([(x, 258), (x, 244)], (60, 50, 66), 2)
    scene.lights.append((CENTER, 300, 150, (200, 50, 120)))

    def glow(pen: _Pen) -> None:
        pen.box((262, 268, 476, 332), outline=NEON, width=2.5, radius=8)
        word, size = "МАГИЯ", 44
        widths = [pen.draw.textlength(char, font=art.font(size)) / SCALE + 5 for char in word]
        x = CENTER - sum(widths) / 2
        for char, width in zip(word, widths):
            if char == "Г":
                pen.text(x + width / 2, 301, char, size, (136, 70, 104))
            else:
                pen.text(x + width / 2, 301, char, size, (255, 226, 240), stroke=1.6, stroke_fill=NEON)
            x += width

    scene.glowing.append(glow)


def _mirror(scene: _Scene, client: str | None) -> None:
    x, y, wide, tall = 584, 356, 56, 92
    scene.pen.oval((x - wide - 10, y - tall - 10, x + wide + 10, y + tall + 10), fill=GOLD_DIM)
    scene.pen.oval((x - wide - 5, y - tall - 5, x + wide + 5, y + tall + 5), outline=GOLD, width=3)

    def glass(mask: _Pen) -> None:
        mask.oval((x - wide, y - tall, x + wide, y + tall), fill=255)

    def reflection(pen: _Pen) -> None:
        pen.box((x - wide, y - tall, x + wide, y + tall), fill=(126, 156, 176))
        pen.poly([(x - 70, y - 20), (x + 10, y - 100), (x + 40, y - 100), (x - 70, y + 20)], fill=(168, 196, 212))
        pen.poly([(x - 70, y + 50), (x + 70, y - 80), (x + 70, y - 62), (x - 70, y + 66)], fill=(150, 180, 198))
        if client is not None:
            # В зеркале клиент со спины: мадам видит то, чего он не показывает.
            pen.oval((x - 26, y + 6, x + 26, y + 70), fill=(38, 32, 48))
            pen.oval((x - 52, y + 58, x + 52, y + 130), fill=(38, 32, 48))

    scene.inside(glass, reflection)
    scene.lights.append((x, y, 90, (60, 80, 100)))


def _reviews(scene: _Scene) -> None:
    pen = scene.pen
    top = 488
    pen.line([(155, top - 12), (120, top)], (40, 30, 26), 1.5)
    pen.line([(155, top - 12), (190, top)], (40, 30, 26), 1.5)
    pen.box((84, top, 226, top + 82), fill=GOLD_DIM, outline=GOLD, width=3)
    pen.box((94, top + 10, 216, top + 72), fill=PAPER)
    pen.text(155, top + 22, "ОТЗЫВЫ", 12, INK)
    for index in range(5):
        pen.poly(_star(111 + index * 22, top + 42, 9), fill=(214, 150, 40))
    for y in (top + 58, top + 65):
        pen.line([(104, y), (206 - (y - top - 58) * 6, y)], (150, 130, 110), 1.5)


def _flyer(scene: _Scene) -> None:
    pen = scene.pen
    left, top, right, bottom = 98, 586, 204, 720
    pen.box((left, top, right, bottom - 30), fill=PAPER)
    pen.text((left + right) / 2, top + 22, "ГАДАЮ", 19, (150, 30, 40))
    for index, words in enumerate(("верну мужа", "сниму порчу", "оформлю вычет")):
        pen.text((left + right) / 2, top + 46 + index * 15, words, 11, INK)
    strip = (right - left) / 7
    for index in range(7):
        if index == 4:
            continue  # один номерок уже оторвали
        x = left + index * strip
        pen.box((x, bottom - 30, x + strip - 1.5, bottom - (0 if index % 2 else 3)), fill=PAPER)
        pen.line([(x + strip / 2, bottom - 26), (x + strip / 2, bottom - 8)], (150, 130, 110), 1.5)
    pen.disc((left + right) / 2, top + 5, 4, fill=(190, 40, 50))


def _door(scene: _Scene, guarded: bool) -> None:
    pen = scene.pen
    left, top = 590, 486
    pen.box((left - 8, top - 8, RIGHT + 20, FLOOR), fill=WOOD_DARK)
    if not guarded:
        pen.box((left, top, RIGHT + 20, FLOOR), fill=WOOD)
        for y0, y1 in ((top + 16, 610), (628, FLOOR - 18)):
            pen.box((left + 14, y0, RIGHT + 20, y1), outline=WOOD_DARK, width=3)
        pen.disc(left + 16, 640, 6, fill=GOLD)
        return
    # Тётя Зина стоит в проёме, руки на груди.
    pen.box((left, top, RIGHT + 20, FLOOR), fill=(14, 12, 16))
    pen.poly([(left, top), (left + 16, top + 14), (left + 16, FLOOR), (left, FLOOR)], fill=WOOD)
    x = 650
    pen.poly(art.closed_curve([(x - 46, FLOOR + 10), (x - 44, 650), (x - 30, 606), (x, 594), (x + 30, 606), (x + 44, 650), (x + 46, FLOOR + 10)]),
             fill=(72, 86, 136))
    pen.line([(x - 40, 668), (x - 8, 690), (x + 30, 672)], (48, 58, 96), 9)
    pen.line([(x + 40, 668), (x + 8, 692), (x - 28, 676)], (98, 112, 160), 9)
    pen.disc(x, 562, 31, fill=(200, 44, 56))
    pen.oval((x - 22, 546, x + 22, 594), fill=SKIN)
    pen.poly([(x - 4, 592), (x - 20, 612), (x - 2, 604)], fill=(200, 44, 56))
    pen.poly([(x + 4, 592), (x + 20, 612), (x + 2, 604)], fill=(170, 34, 46))
    for dx, dy in ((-18, -22), (0, -27), (18, -22), (-26, -6), (26, -6)):
        pen.disc(x + dx, 562 + dy, 2.2, fill=(250, 236, 220))
    pen.line([(x - 15, 560), (x - 5, 564)], INK, 2.4)
    pen.line([(x + 15, 560), (x + 5, 564)], INK, 2.4)
    pen.disc(x - 9, 569, 2.2, fill=INK)
    pen.disc(x + 9, 569, 2.2, fill=INK)
    pen.line([(x - 7, 583), (x + 7, 583)], (150, 70, 70), 2)


# --- подарки клиентов -------------------------------------------------------

def _gift_notebook(pen: _Pen, x: float, y: float) -> None:
    pen.box((x - 15, y - 46, x + 15, y), fill=(40, 96, 74))
    for dx in range(-12, 13, 6):
        pen.disc(x + dx, y - 46, 2.4, outline=(200, 200, 206), width=1.2)
    pen.box((x - 9, y - 32, x + 9, y - 20), fill=PAPER)
    pen.line([(x + 6, y - 6), (x + 20, y - 40)], GOLD, 2.4)


def _gift_binoculars(pen: _Pen, x: float, y: float) -> None:
    for dx in (-10, 10):
        pen.box((x + dx - 8, y - 36, x + dx + 8, y), fill=(34, 34, 44), radius=3)
        pen.oval((x + dx - 9, y - 42, x + dx + 9, y - 30), fill=(120, 160, 200), outline=(34, 34, 44), width=2)
    pen.box((x - 4, y - 26, x + 4, y - 16), fill=(60, 60, 72))


def _gift_card(pen: _Pen, x: float, y: float) -> None:
    pen.box((x - 12, y - 6, x + 12, y), fill=WOOD_DARK)
    pen.poly(_slab((x, y - 22), 40, 26, -6), fill=(246, 242, 232))
    pen.line(_turned([(x - 14, y - 26), (x + 6, y - 26)], (x, y - 22), -6), GOLD_DIM, 2.4)
    pen.line(_turned([(x - 14, y - 18), (x + 12, y - 18)], (x, y - 22), -6), (150, 140, 130), 1.4)


def _gift_rose(pen: _Pen, x: float, y: float) -> None:
    pen.poly([(x - 8, y), (x - 6, y - 24), (x + 6, y - 24), (x + 8, y)], fill=(120, 150, 170))
    pen.line([(x, y - 22), (x - 2, y - 40), (x + 3, y - 52)], (74, 90, 50), 2.2)
    pen.poly([(x - 2, y - 38), (x - 14, y - 44), (x - 4, y - 32)], fill=(74, 90, 50))
    pen.disc(x + 3, y - 56, 9, fill=(134, 28, 46))
    pen.arc((x - 2, y - 61, x + 8, y - 51), 30, 300, (88, 14, 28), 1.6)


def _gift_jar(pen: _Pen, x: float, y: float) -> None:
    pen.box((x - 15, y - 42, x + 15, y), fill=(156, 192, 150), radius=5)
    for dx, dy, turn in ((-6, -16, 70), (5, -24, 100), (-2, -30, 84), (7, -9, 60)):
        pen.poly(_slab((x + dx, y + dy), 22, 8, turn), fill=(60, 110, 50))
    pen.line([(x - 10, y - 36), (x - 10, y - 8)], (210, 232, 208), 2)
    pen.box((x - 14, y - 50, x + 14, y - 41), fill=(200, 60, 52), radius=2)


def _gift_lamp(pen: _Pen, x: float, y: float) -> None:
    pen.box((x - 11, y - 4, x + 11, y), fill=(40, 40, 48))
    pen.line([(x, y - 2), (x, y - 24)], (40, 40, 48), 3)
    pen.disc(x, y - 40, 17, outline=(250, 246, 232), width=5)
    pen.disc(x, y - 40, 19, outline=(170, 166, 160), width=1.2)


def _gift_foil(pen: _Pen, x: float, y: float) -> None:
    pen.poly([(x - 20, y), (x - 2, y - 48), (x + 20, y)], fill=(196, 202, 212))
    pen.poly([(x - 2, y - 48), (x + 4, y), (x + 20, y)], fill=(150, 158, 170))
    pen.line([(x - 12, y - 20), (x - 2, y - 14), (x + 8, y - 24)], (236, 240, 246), 1.4)
    pen.line([(x - 2, y - 48), (x + 6, y - 60)], (150, 158, 170), 1.6)
    pen.disc(x + 6, y - 61, 2.4, fill=(220, 60, 60))


def _gift_gradebook(pen: _Pen, x: float, y: float) -> None:
    pen.poly(_slab((x, y - 22), 30, 44, 5), fill=(44, 78, 160))
    pen.poly(_slab((x + 1, y - 30), 12, 10, 5), fill=GOLD)
    pen.line(_turned([(x - 9, y - 14), (x + 9, y - 14)], (x, y - 22), 5), (200, 210, 236), 1.6)
    pen.line(_turned([(x - 9, y - 8), (x + 9, y - 8)], (x, y - 22), 5), (200, 210, 236), 1.6)


def _gift_gold(pen: _Pen, x: float, y: float) -> None:
    pen.poly([(x - 22, y), (x - 15, y - 20), (x + 15, y - 20), (x + 22, y)], fill=(214, 164, 50))
    pen.poly([(x - 15, y - 20), (x - 10, y - 26), (x + 20, y - 26), (x + 15, y - 20)], fill=(250, 216, 110))
    pen.poly([(x + 15, y - 20), (x + 20, y - 26), (x + 27, y - 6), (x + 22, y)], fill=(170, 124, 34))
    pen.text(x, y - 10, "999", 10, (130, 92, 20))


def _gift_horseshoe(pen: _Pen, x: float, y: float) -> None:
    pen.line([(x, y), (x, y - 12)], WOOD_DARK, 2)
    pen.arc((x - 17, y - 48, x + 17, y - 10), -40, 220, (176, 180, 192), 7)
    for turn in (20, 90, 160):
        pen.disc(x + math.cos(math.radians(turn)) * 14, y - 29 + math.sin(math.radians(turn)) * 16, 1.8, fill=(70, 72, 84))


_GIFTS: dict[str, Callable[[_Pen, float, float], None]] = {
    "skeptic": _gift_notebook,
    "jealous": _gift_binoculars,
    "businessman": _gift_card,
    "romantic": _gift_rose,
    "granny": _gift_jar,
    "blogger": _gift_lamp,
    "conspiracy": _gift_foil,
    "student": _gift_gradebook,
    "tycoon": _gift_gold,
    "desperate": _gift_horseshoe,
}


def _shelf(scene: _Scene, gifts: Sequence[str]) -> None:
    """Полка во всю стену. Подарки рисуются поверх теней и крупнее: это коллекция, её должно быть видно."""
    pen = scene.pen
    pen.box((SHELF_LEFT, SHELF, SHELF_RIGHT, SHELF + 9), fill=WOOD)
    pen.line([(SHELF_LEFT, SHELF + 9), (SHELF_RIGHT, SHELF + 9)], WOOD_DARK, 2)
    for x in (SHELF_LEFT + 38, SHELF_RIGHT - 38):
        pen.poly([(x - 8, SHELF + 9), (x + 8, SHELF + 9), (x, SHELF + 26)], fill=WOOD_DARK)
    step = (SHELF_RIGHT - SHELF_LEFT - 16) / len(_GIFTS)
    for index, key in enumerate(_GIFTS):
        if key in gifts:
            x = SHELF_LEFT + 8 + step / 2 + index * step
            scene.lights.append((x, SHELF - 30, 70, (70, 56, 34)))  # тёплая подсветка: вещь не висит в темноте
            scene.glowing.append(lambda glow, key=key, x=x: _GIFTS[key](_Zoom(glow, (x, SHELF), GIFT_ZOOM), x, SHELF))


# --- кресло и клиент --------------------------------------------------------

def _seat(scene: _Scene, armchair: bool) -> None:
    pen = scene.pen
    if not armchair:
        for x in (298, 440):
            pen.box((x - 7, 536, x + 7, 840), fill=WOOD)
        pen.box((290, 530, 448, 556), fill=WOOD, radius=8)
        for y in (600, 660):
            pen.box((298, y, 440, y + 14), fill=WOOD_DARK)
        return
    back = [(226, 850), (220, 560), (258, 436), (CENTER, 396), (480, 436), (518, 560), (512, 850)]
    pen.poly(art.closed_curve(back), fill=VELVET)
    inner = [(CENTER + (x - CENTER) * 0.8, 440 + (y - 396) * 0.9) for x, y in back]
    pen.poly(art.closed_curve(inner), fill=VELVET_LIGHT)
    for row, y in enumerate(range(480, 800, 46)):
        for x in range(282 + (0 if row % 2 else 29), 460, 58):
            pen.disc(x, y, 4, fill=VELVET)
    for x in (212, 526):
        pen.box((x - 34, 690, x + 34, 860), fill=VELVET, radius=30)
        pen.box((x - 26, 698, x + 26, 730), fill=VELVET_LIGHT, radius=14)


def _bust(scene: _Scene, hair: Callable[[_Pen], None] | None = None) -> None:
    """Силуэт клиента за столом: голова, плечи и тёплый контур от свечей."""
    left = [(196, 880), (204, 742), (246, 690), (318, 664), (342, 640), (346, 590)]
    right = [(CENTER * 2 - x, y) for x, y in reversed(left)]
    mask = Image.new("L", scene.picture.size, 0)
    shape = _Pen(mask)
    shape.poly(art.closed_curve([*left, *right]), fill=255)
    shape.oval((311, 446, 427, 592), fill=255)
    if hair is not None:
        hair(shape)
    halo = mask.resize((WIDTH, HEIGHT)).filter(ImageFilter.GaussianBlur(9)).resize(mask.size).point(lambda value: value * 2 // 3)
    scene.picture.paste((255, 214, 160), mask=halo)
    scene.picture.paste(BODY, mask=mask.filter(ImageFilter.GaussianBlur(1.5)))
    scene.pen = _Pen(scene.picture)


def _eyes(pen: _Pen, y: float = 516, color: Color = (236, 226, 204)) -> None:
    for x in (349, 389):
        pen.oval((x - 6, y - 4, x + 6, y + 4), fill=color)


def _client_guest(scene: _Scene) -> None:
    _bust(scene)
    scene.glowing.append(_eyes)


def _client_skeptic(scene: _Scene) -> None:
    _bust(scene)

    def glasses(pen: _Pen) -> None:
        for x in (346, 392):
            pen.disc(x, 516, 17, fill=(150, 176, 196), outline=(226, 214, 190), width=3)
            pen.line([(x - 9, 522), (x + 3, 508)], (226, 236, 244), 2.4)
        pen.line([(363, 514), (375, 514)], (226, 214, 190), 3)
        pen.line([(334, 492), (358, 486)], (226, 214, 190), 3)  # бровь домиком
        # блокнот прижат к груди
        pen.poly(_slab((452, 748), 62, 80, 12), fill=PAPER)
        for step in range(4):
            pen.line(_turned([(430, 728 + step * 13), (474, 728 + step * 13)], (452, 748), 12), (120, 110, 104), 1.6)
        pen.line([(486, 700), (462, 760)], (60, 90, 170), 4)

    scene.glowing.append(glasses)


def _client_jealous(scene: _Scene) -> None:
    locks = [(288, 650), (284, 500), (312, 428), (CENTER, 404), (426, 428), (454, 500), (450, 650), (474, 730), (264, 730)]

    def hair(shape: _Pen) -> None:
        shape.poly(art.closed_curve(locks), fill=255)

    _bust(scene, hair)

    def details(pen: _Pen) -> None:
        pen.poly(art.closed_curve(locks), fill=(124, 36, 44))
        pen.oval((318, 452, 420, 596), fill=BODY)
        pen.poly(art.closed_curve([(330, 640), (CENTER, 610), (408, 640), (420, 740), (318, 740)]), fill=BODY)
        pen.line([(336, 502), (360, 510)], (236, 226, 204), 3)  # брови сведены
        pen.line([(402, 502), (378, 510)], (236, 226, 204), 3)
        _eyes(pen, 522)
        pen.oval((356, 558, 382, 568), fill=(214, 44, 64))
        for x in (322, 416):
            pen.disc(x, 584, 7, fill=(250, 196, 70))

    scene.glowing.append(details)


def _client_businessman(scene: _Scene) -> None:
    _bust(scene)

    def details(pen: _Pen) -> None:
        pen.poly([(336, 652), (CENTER, 700), (402, 652), (388, 640), (350, 640)], fill=(236, 232, 224))
        pen.poly([(CENTER - 12, 668), (CENTER + 12, 668), (CENTER + 17, 790), (CENTER, 812), (CENTER - 17, 790)], fill=(170, 34, 44))
        pen.poly([(CENTER - 11, 664), (CENTER + 11, 664), (CENTER + 7, 684), (CENTER - 7, 684)], fill=(130, 22, 32))
        _eyes(pen)

    scene.glowing.append(details)


def _client_romantic(scene: _Scene) -> None:
    def hair(shape: _Pen) -> None:
        shape.disc(CENTER, 432, 34, fill=255)
        shape.oval((298, 452, 440, 560), fill=255)

    _bust(scene, hair)

    def details(pen: _Pen) -> None:
        _eyes(pen, 520)
        for x in (336, 402):
            pen.disc(x, 540, 9, fill=(200, 90, 110))  # румянец
        for x, y, size in ((470, 440, 20), (506, 392, 14), (250, 470, 12)):
            pen.disc(x - size * 0.5, y, size * 0.58, fill=(250, 110, 150))
            pen.disc(x + size * 0.5, y, size * 0.58, fill=(250, 110, 150))
            pen.poly([(x - size * 1.05, y + size * 0.22), (x + size * 1.05, y + size * 0.22), (x, y + size * 1.3)], fill=(250, 110, 150))
        pen.disc(396, 420, 9, fill=(250, 210, 90))
        for turn in range(0, 360, 72):
            pen.disc(396 + math.cos(math.radians(turn)) * 11, 420 + math.sin(math.radians(turn)) * 11, 6, fill=(250, 246, 236))

    scene.glowing.append(details)


def _client_granny(scene: _Scene) -> None:
    scarf = [(288, 560), (286, 470), (324, 420), (CENTER, 404), (414, 420), (452, 470), (450, 560), (420, 612), (CENTER, 640), (318, 612)]

    def hair(shape: _Pen) -> None:
        shape.poly(art.closed_curve(scarf), fill=255)

    _bust(scene, hair)

    def details(pen: _Pen) -> None:
        pen.poly(art.closed_curve(scarf), fill=(150, 70, 150))
        pen.oval((320, 452, 418, 592), fill=BODY)
        for x, y in ((300, 500), (438, 500), (330, 432), (408, 432), (CENTER, 418), (306, 566), (432, 566)):
            pen.disc(x, y, 5, fill=(250, 210, 90))
        pen.poly([(CENTER, 626), (336, 676), (360, 690)], fill=(128, 54, 128))
        pen.poly([(CENTER, 626), (402, 676), (378, 690)], fill=(128, 54, 128))
        for x in (350, 388):
            pen.disc(x, 520, 13, outline=(226, 214, 190), width=2.4)
            pen.oval((x - 5, 517, x + 5, 523), fill=(236, 226, 204))
        pen.line([(363, 520), (375, 520)], (226, 214, 190), 2.4)

    scene.glowing.append(details)


def _client_blogger(scene: _Scene) -> None:
    def hair(shape: _Pen) -> None:
        shape.poly(art.closed_curve([(414, 440), (470, 430), (500, 480), (492, 560), (468, 600), (462, 520), (430, 480)]), fill=255)
        shape.poly([(470, 760), (500, 640), (524, 560), (552, 566), (540, 660), (520, 790)], fill=255)  # рука с телефоном

    _bust(scene, hair)
    scene.lights.append((520, 520, 130, (120, 170, 255)))

    def details(pen: _Pen) -> None:
        _eyes(pen)
        pen.oval((356, 552, 382, 562), fill=(240, 90, 130))
        pen.disc(420, 446, 9, fill=(250, 110, 150))  # резинка на хвосте
        pen.poly(_slab((540, 520), 46, 86, 10), fill=(20, 20, 28))
        pen.poly(_slab((540, 520), 38, 74, 10), fill=(170, 214, 255))
        pen.disc(540, 520, 9, fill=(250, 110, 150))
        pen.disc(538, 446, 34, outline=(255, 250, 236), width=6)  # кольцевая лампа

    scene.glowing.append(details)


def _client_conspiracy(scene: _Scene) -> None:
    def hair(shape: _Pen) -> None:
        shape.poly([(296, 478), (CENTER, 372), (442, 478)], fill=255)

    _bust(scene, hair)

    def details(pen: _Pen) -> None:
        pen.poly([(296, 478), (CENTER, 372), (442, 478)], fill=(198, 204, 214))
        pen.poly([(CENTER, 372), (386, 478), (442, 478)], fill=(150, 158, 172))
        pen.line([(316, 462), (348, 440), (372, 458), (404, 432)], (240, 244, 250), 2)
        pen.line([(CENTER, 372), (384, 338)], (150, 158, 172), 3)
        pen.disc(385, 336, 5, fill=(230, 60, 60))
        for x in (349, 389):
            pen.disc(x, 518, 9, fill=(236, 226, 204))
            pen.disc(x + 3, 518, 3.4, fill=BODY)  # косится в сторону

    scene.glowing.append(details)


def _client_student(scene: _Scene) -> None:
    def hair(shape: _Pen) -> None:
        shape.box((296, 492, 320, 548), fill=255, radius=10)
        shape.box((418, 492, 442, 548), fill=255, radius=10)
        for x in range(322, 420, 14):  # вихры
            shape.poly([(x - 9, 462), (x, 426), (x + 9, 462)], fill=255)

    _bust(scene, hair)

    def details(pen: _Pen) -> None:
        pen.arc((300, 428, 438, 600), 180, 360, (60, 190, 180), 7)
        for x in (296, 418):
            pen.box((x, 492, x + 24, 548), fill=(60, 190, 180), radius=10)
        _eyes(pen)
        pen.line([(338, 536), (348, 540), (360, 536)], (120, 110, 130), 2)  # мешки под глазами
        pen.line([(378, 536), (390, 540), (400, 536)], (120, 110, 130), 2)

    scene.glowing.append(details)


def _client_tycoon(scene: _Scene) -> None:
    def hair(shape: _Pen) -> None:
        shape.poly(art.closed_curve([(196, 880), (190, 740), (240, 676), (316, 650), (CENTER, 690), (422, 650), (498, 676), (548, 740), (542, 880)]), fill=255)

    _bust(scene, hair)

    def details(pen: _Pen) -> None:
        for x in (346, 392):
            pen.box((x - 19, 504, x + 19, 528), fill=(12, 12, 18), outline=GOLD, width=2.4, radius=7)
        pen.line([(365, 512), (373, 512)], GOLD, 3)
        pen.arc((306, 600, 432, 760), 20, 160, GOLD, 7)
        pen.poly([(CENTER, 756), (CENTER - 15, 778), (CENTER, 804), (CENTER + 15, 778)], fill=(150, 226, 250))
        pen.poly([(CENTER, 756), (CENTER - 15, 778), (CENTER, 782)], fill=(226, 248, 255))
        pen.line([(392, 566), (428, 558)], (124, 80, 50), 7)  # сигара
        pen.disc(430, 557, 4, fill=(255, 140, 60))

    scene.glowing.append(details)
    scene.lights.append((430, 557, 26, (255, 120, 40)))


def _client_desperate(scene: _Scene) -> None:
    def hair(shape: _Pen) -> None:
        for x, tall in ((322, 30), (340, 44), (360, 34), (380, 46), (400, 32), (418, 40)):
            shape.poly([(x - 12, 466), (x + rngless(x), 466 - tall), (x + 12, 466)], fill=255)

    def rngless(x: int) -> int:
        return (x * 7) % 15 - 7  # вихры торчат вразнобой, но всегда одинаково

    _bust(scene, hair)

    def details(pen: _Pen) -> None:
        pen.line([(336, 508), (360, 516)], (236, 226, 204), 3)  # брови домиком наоборот
        pen.line([(402, 508), (378, 516)], (236, 226, 204), 3)
        _eyes(pen, 526)
        pen.poly([(346, 534), (340, 552), (352, 552)], fill=(140, 200, 250))
        pen.arc((352, 556, 386, 580), 200, 340, (236, 226, 204), 2.6)
        # личная тучка
        for dx, dy, radius in ((-44, 0, 24), (-14, -14, 30), (22, -8, 28), (50, 4, 20)):
            pen.disc(CENTER + dx, 378 + dy, radius, fill=(124, 128, 146))
        pen.box((CENTER - 50, 378, CENTER + 56, 402), fill=(124, 128, 146), radius=10)
        for dx in (-34, -6, 22, 44):
            pen.line([(CENTER + dx, 412), (CENTER + dx - 5, 430)], (140, 200, 250), 2.6)

    scene.glowing.append(details)


CLIENTS: dict[str, Callable[[_Scene], None]] = {
    GUEST: _client_guest,
    "skeptic": _client_skeptic,
    "jealous": _client_jealous,
    "businessman": _client_businessman,
    "romantic": _client_romantic,
    "granny": _client_granny,
    "blogger": _client_blogger,
    "conspiracy": _client_conspiracy,
    "student": _client_student,
    "tycoon": _client_tycoon,
    "desperate": _client_desperate,
}


# --- стол и то, что на нём --------------------------------------------------

def _table(scene: _Scene) -> None:
    pen = scene.pen
    pen.oval((24, 818, 714, 1140), fill=CLOTH_DARK)
    pen.oval((30, 812, 708, 1130), fill=CLOTH)
    pen.oval((64, 838, 674, 1100), outline=_mix(CLOTH, GOLD, 0.45), width=2)
    for turn in range(200, 341, 10):
        x = CENTER + math.cos(math.radians(turn)) * 322
        y = 971 + math.sin(math.radians(turn)) * 146
        pen.disc(x, y, 3, fill=_mix(CLOTH, GOLD, 0.6))


def _flame(pen: _Pen, x: float, y: float, size: float = 1.0) -> None:
    pen.poly(art.closed_curve([(x, y - 24 * size), (x + 7 * size, y - 8 * size), (x, y), (x - 7 * size, y - 8 * size)]), fill=FLAME)
    pen.poly(art.closed_curve([(x, y - 14 * size), (x + 3 * size, y - 5 * size), (x, y - 1), (x - 3 * size, y - 5 * size)]), fill=(255, 246, 214))


def _candles(scene: _Scene) -> None:
    pen = scene.pen
    for x, base, tall in ((212, 902, 58), (240, 912, 84), (268, 900, 46)):
        pen.oval((x - 15, base - 5, x + 15, base + 7), fill=GOLD_DIM)
        pen.box((x - 8, base - tall, x + 8, base), fill=WAX)
        pen.oval((x - 8, base - tall - 3, x + 8, base - tall + 3), fill=(250, 242, 220))
        pen.line([(x - 8, base - tall + 6), (x - 8, base - tall + 22)], (250, 242, 220), 4)  # потёк воска
        pen.line([(x, base - tall), (x, base - tall - 6)], INK, 1.6)
        scene.lights.append((x, base - tall - 16, 84, (190, 104, 40)))
        scene.glowing.append(lambda glow, x=x, y=base - tall - 5: _flame(glow, x, y))


def _ball(scene: _Scene, real: bool) -> None:
    pen = scene.pen
    x, y, radius = CENTER, 866, 50
    pen.poly([(x - 40, 936), (x - 24, 908), (x + 24, 908), (x + 40, 936)], fill=GOLD_DIM if real else WOOD_DARK)
    pen.oval((x - 42, 928, x + 42, 944), fill=GOLD if real else WOOD)
    if not real:
        # Старый «шар» оказался плафоном от люстры.
        pen.disc(x, y, radius, fill=(176, 172, 160))
        pen.disc(x - 10, y - 10, radius - 16, fill=(196, 192, 180))
        pen.line([(x + 6, y - 46), (x + 16, y - 20), (x + 8, y - 4), (x + 22, y + 18)], (110, 106, 100), 1.6)
        pen.box((x - 14, y + 40, x + 14, y + 50), fill=(120, 116, 108))
        return
    for step in range(8):
        share = step / 7
        pen.disc(x - 14 * share, y - 14 * share, radius * (1 - 0.72 * share), fill=_mix((62, 44, 140), (190, 170, 255), share))
    scene.lights.append((x, y, 230, (120, 90, 230)))

    def glow(light: _Pen) -> None:
        light.arc((x - radius + 8, y - radius + 8, x + radius - 8, y + radius - 8), 190, 262, (240, 236, 255), 4)
        for dx, dy, size in ((12, 10, 5), (-18, 18, 3.4), (22, -12, 3), (-4, -22, 2.6)):
            light.poly(_star(x + dx, y + dy, size, points=4, inner=0.3), fill=(255, 250, 236))
        light.disc(x, y, radius, outline=(206, 190, 255), width=2)

    scene.glowing.append(glow)


def _tea(scene: _Scene) -> None:
    pen = scene.pen
    x, y = 498, 880
    blue = (48, 84, 170)
    pen.poly([(x + 28, y - 4), (x + 60, y - 26), (x + 64, y - 18), (x + 34, y + 12)], fill=(240, 240, 236))  # носик
    pen.arc((x - 58, y - 22, x - 20, y + 20), 80, 280, (240, 240, 236), 6)  # ручка
    pen.oval((x - 36, y - 28, x + 36, y + 30), fill=(244, 244, 240))
    pen.oval((x - 20, y - 36, x + 20, y - 22), fill=(244, 244, 240), outline=blue, width=2)
    pen.disc(x, y - 38, 5, fill=blue)
    for turn in range(0, 360, 60):
        pen.disc(x + math.cos(math.radians(turn)) * 12, y + 2 + math.sin(math.radians(turn)) * 12, 5, fill=blue)
    pen.disc(x, y + 2, 5, fill=(240, 200, 70))
    cup = 578
    pen.oval((cup - 28, 918, cup + 28, 934), fill=(244, 244, 240))  # блюдце
    pen.chord((cup - 20, 880, cup + 20, 930), 0, 180, (244, 244, 240))
    pen.oval((cup - 20, 898, cup + 20, 912), fill=(150, 90, 40), outline=blue, width=2)
    for dx in (-7, 6):
        pen.line(art.catmull([(cup + dx, 892), (cup + dx + 6, 878), (cup + dx - 4, 864), (cup + dx + 4, 850)], 6), (216, 216, 226), 2)


def _bowl(scene: _Scene) -> None:
    pen = scene.pen
    x, y = 640, 982
    pen.chord((x - 40, y - 40, x + 40, y + 34), 0, 180, (150, 104, 44))
    pen.oval((x - 40, y - 12, x + 40, y + 8), fill=(214, 164, 70), outline=(110, 74, 30), width=2)
    pen.oval((x - 30, y - 8, x + 30, y + 4), fill=(96, 62, 26))
    pen.line([(x + 4, y - 4), (x + 34, y - 50)], WOOD, 6)
    pen.disc(x + 35, y - 52, 6, fill=(170, 60, 50))
    for radius in (54, 68):
        pen.arc((x - radius, y - radius - 4, x + radius, y + radius - 4), 212, 250, _mix(CLOTH, GOLD, 0.8), 2)


def _deck(scene: _Scene) -> None:
    pen = scene.pen
    for turn, shift in ((-26, -26), (-9, -9), (9, 9)):
        card = _slab((290 + shift, 992 - abs(shift) * 0.2), 46, 70, turn)
        pen.poly(card, fill=(34, 40, 104), outline=GOLD)
        pen.line([*card, card[0]], GOLD, 2.4)
    top = _slab((316, 990), 46, 70, 24)
    pen.poly(top, fill=PAPER)
    pen.line([*top, top[0]], GOLD, 2.6)
    pen.poly(_turned(_star(316, 990, 15), (316, 990), 24), fill=(170, 40, 60))
    pen.disc(316, 990, 4, fill=GOLD)


def _loupe(scene: _Scene) -> None:
    pen = scene.pen
    x, y = 446, 978
    pen.line([(x + 18, y + 18), (x + 52, y + 50)], WOOD_DARK, 9)
    pen.disc(x, y, 26, fill=(176, 208, 224))
    pen.disc(x, y, 27, outline=GOLD, width=5)
    pen.arc((x - 18, y - 18, x + 18, y + 18), 200, 260, (244, 250, 255), 3)


def _almanac(scene: _Scene) -> None:
    pen = scene.pen
    center = (154, 968)
    pen.poly(_slab((center[0] + 3, center[1] + 5), 100, 70, -14), fill=(226, 220, 204))
    pen.poly(_slab(center, 100, 70, -14), fill=(30, 40, 96))
    pen.poly(_slab(center, 88, 58, -14), outline=GOLD_DIM)
    pen.disc(center[0], center[1], 18, fill=(246, 222, 130))
    pen.disc(center[0] + 9, center[1] - 6, 16, fill=(30, 40, 96))
    for dx, dy in ((30, -16), (-32, 14), (26, 18)):
        pen.poly(_star(center[0] + dx, center[1] + dy, 4, points=4, inner=0.3), fill=(246, 222, 130))


def _ledger(scene: _Scene) -> None:
    pen = scene.pen
    center = (530, 1004)
    pen.poly(_slab(center, 92, 64, 11), fill=(84, 124, 96))
    pen.poly(_slab((center[0] - 38, center[1] - 7), 12, 64, 11), fill=(40, 46, 44))
    pen.poly(_slab((center[0] + 6, center[1] - 6), 50, 22, 11), fill=PAPER)
    pen.text(center[0] + 6, center[1] - 6, "кто с кем", 11, INK)
    pen.line([(center[0] - 20, center[1] + 30), (center[0] + 50, center[1] + 2)], (240, 190, 60), 5)
    pen.line([(center[0] + 50, center[1] + 2), (center[0] + 58, center[1] - 1)], (240, 150, 150), 5)


def _cat(scene: _Scene) -> None:
    pen = scene.pen
    fur = (14, 12, 18)
    x = 116
    pen.line(art.catmull([(150, 900), (190, 916), (214, 900), (206, 880)], 8), fur, 11)
    pen.oval((x - 46, 792, x + 46, 912), fill=fur)
    pen.disc(x, 776, 32, fill=fur)
    pen.poly([(x - 30, 766), (x - 26, 728), (x - 6, 752)], fill=fur)
    pen.poly([(x + 30, 766), (x + 26, 728), (x + 6, 752)], fill=fur)

    def eyes(glow: _Pen) -> None:
        for dx in (-13, 13):
            glow.oval((x + dx - 8, 770, x + dx + 8, 782), fill=(214, 226, 84))
            glow.oval((x + dx - 2, 770, x + dx + 2, 782), fill=fur)
        glow.poly([(x - 3, 788), (x + 3, 788), (x, 792)], fill=(214, 140, 150))
        for side in (-1, 1):
            for tilt in (-4, 2):
                glow.line([(x + side * 10, 792), (x + side * 40, 792 + tilt)], (150, 150, 160), 1.2)

    scene.glowing.append(eyes)


# --- свет и сборка ----------------------------------------------------------

def _shade(picture: Image.Image, lights: Sequence[Light]) -> Image.Image:
    """Полумрак салона: темно по углам, светлее у стола, плюс цветные пятна от ламп и свечей."""
    size = (WIDTH // 2, HEIGHT // 2)  # свет мягкий, мелкий холст ему не вредит
    shade = Image.new("L", size, 112)
    draw = ImageDraw.Draw(shade)
    draw.ellipse((40, 180, 329, 540), fill=188)
    shade = shade.filter(ImageFilter.GaussianBlur(46))
    glow = Image.new("RGB", size, (0, 0, 0))
    for x, y, radius, color in lights:
        spot = Image.new("L", size, 0)
        ImageDraw.Draw(spot).ellipse(((x - radius * 0.6) / 2, (y - radius * 0.6) / 2, (x + radius * 0.6) / 2, (y + radius * 0.6) / 2), fill=200)
        spot = spot.filter(ImageFilter.GaussianBlur(radius / 5))
        layer = Image.merge("RGB", [spot.point(lambda value, channel=channel: value * channel // 255) for channel in color])
        glow = ImageChops.add(glow, layer)
    dimmed = ImageChops.multiply(picture, Image.merge("RGB", [shade.resize(picture.size, Image.BILINEAR)] * 3))
    return ImageChops.screen(dimmed, glow.resize(picture.size, Image.BILINEAR))


def title(items: Iterable[str]) -> str:
    return salon.rank(len(salon.owned_items(items))).upper()


def render(items: Iterable[str] = (), client: str | None = None, gifts: Iterable[str] = ()) -> bytes:
    """Нарисовать салон. ``items`` — купленное в лавке, ``client`` — кто сидит напротив, ``gifts`` — подарки на полке."""
    if client is not None and client not in CLIENTS:
        raise ValueError(f"неизвестный клиент: {client}")
    owned = {item.key for item in salon.owned_items(items)}
    shelf = [key for key in _GIFTS if key in set(gifts)]
    rng = random.Random(23)
    image, picture, box = art.canvas(title(owned), rng)
    sky = picture.crop((84 * SCALE, 262 * SCALE, 236 * SCALE, 462 * SCALE))
    scene = _Scene(picture)

    _wall(scene, len(owned), rng)
    _window(scene, sky)
    if "neon" in owned:
        _neon(scene)
    if "mirror" in owned:
        _mirror(scene, client)
    if "reviews" in owned:
        _reviews(scene)
    if "flyers" in owned:
        _flyer(scene)
    _door(scene, "zina" in owned)
    if shelf:
        _shelf(scene, shelf)
    _seat(scene, "armchair" in owned)
    if client is not None:
        CLIENTS[client](scene)
    _table(scene)
    if "cat" in owned:
        _cat(scene)
    if "candles" in owned:
        _candles(scene)
    _ball(scene, "ball" in owned)
    if "tea" in owned:
        _tea(scene)
    if "bowl" in owned:
        _bowl(scene)
    if "almanac" in owned:
        _almanac(scene)
    if "deck" in owned:
        _deck(scene)
    if "loupe" in owned:
        _loupe(scene)
    if "ledger" in owned:
        _ledger(scene)

    lit = _shade(scene.picture, scene.lights)
    pen = _Pen(lit)
    for glow in scene.glowing:
        glow(pen)
    return art.finish(image, lit, box)
