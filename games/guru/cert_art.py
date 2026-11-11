"""Справка о живом приёме и диплом о смене: картинки, которые хочется переслать.

Обе — лист бумаги на ночном небе, в той же рамке, что карты, ладонь и аура. На справке —
что мадам предсказала, насколько уверенно и что решил клиент: его приговор стоит штампом.
На дипломе — звание и итог смены с печатью салона. Модуль получает готовые данные
(``Certificate``, ``Diploma``) и отдаёт JPEG; ``brand`` — подпись под листом, по которой
переславшие находят игру.
"""

from __future__ import annotations

import math
import random

from PIL import Image, ImageChops, ImageDraw, ImageFont

from games.guru import art
from games.guru import content as C
from games.guru.art import SCALE
from games.guru.contract import Certificate, Diploma
from games.guru.rules import signed_rub

CERTIFICATE_TITLE = "СПРАВКА"
DIPLOMA_TITLE = "ДИПЛОМ"
SALON = "САЛОН «ГУРУ»"
ANONYMOUS = "Аноним"

PAPER = (92, 148, 646, 992)
CENTER_X = 369
TEXT_WIDTH = 478
PAPER_COLOR = (236, 221, 188)
PAPER_EDGE = (178, 152, 110)
SHADOW = (10, 10, 24)
INK = (48, 36, 30)
INK_DIM = (124, 102, 78)
INK_TITLE = (126, 38, 38)
INK_PEN = (38, 50, 116)
STAMP_COLORS = {
    "hit": (30, 122, 72),
    "partial": (176, 116, 24),
    "miss": (86, 96, 124),
    "exposure": (176, 34, 34),
}
SEAL_COLOR = (150, 44, 52)
LEADING = 1.28
WORDS = (520, 744)  # между «предсказала:» и приговором: сюда встают предсказание и уверенность
Color = tuple[int, int, int]


def _px(value: float) -> int:
    return round(value * SCALE)


def _sheet(picture: Image.Image) -> ImageDraw.ImageDraw:
    """Лист бумаги с тенью и двойной рамкой. Возвращает перо, которым по нему пишут."""
    draw = ImageDraw.Draw(picture)
    left, top, right, bottom = (_px(value) for value in PAPER)
    draw.rectangle((left + _px(10), top + _px(12), right + _px(10), bottom + _px(12)), fill=SHADOW)
    draw.rectangle((left, top, right, bottom), fill=PAPER_COLOR)
    for inset, width in ((12, 2), (19, 1)):
        draw.rectangle((left + _px(inset), top + _px(inset), right - _px(inset), bottom - _px(inset)),
                       outline=PAPER_EDGE, width=_px(width))
    return draw


def _fit(draw: ImageDraw.ImageDraw, text: str, size: int, width: int = TEXT_WIDTH) -> ImageFont.FreeTypeFont:
    """Шрифт, с которым строка влезает в лист: длинное имя уменьшается, а не обрезается."""
    while size > 16 and draw.textlength(text, font=art.font(size)) > _px(width):
        size -= 2
    return art.font(size)


def _line(draw: ImageDraw.ImageDraw, y: float, text: str, size: int, fill: Color = INK, *,
          x: float = CENTER_X, width: int = TEXT_WIDTH) -> None:
    draw.text((_px(x), _px(y)), text, font=_fit(draw, text, size, width), fill=fill, anchor="mm")


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        longer = f"{line} {word}".strip()
        if line and draw.textlength(longer, font=font) > _px(TEXT_WIDTH):
            lines.append(line)
            line = word
        else:
            line = longer
    return [*lines, line] if line else lines


def _paragraph(draw: ImageDraw.ImageDraw, text: str, size: int, max_lines: int) -> tuple[int, list[str]]:
    """Разбить на строки, уменьшая шрифт, пока абзац не уложится в ``max_lines``."""
    lines = _wrap(draw, text, art.font(size))
    while len(lines) > max_lines and size > 18:
        size -= 2
        lines = _wrap(draw, text, art.font(size))
    return size, lines


def _block(draw: ImageDraw.ImageDraw, y: float, size: int, lines: list[str], fill: Color = INK) -> float:
    """Строки по центру, первая на высоте ``y``. Возвращает высоту последней."""
    for index, line in enumerate(lines):
        draw.text((_px(CENTER_X), _px(y + index * size * LEADING)), line, font=art.font(size), fill=fill, anchor="mm")
    return y + (len(lines) - 1) * size * LEADING


def _rule(draw: ImageDraw.ImageDraw, y: float, half: float = 170) -> None:
    draw.line((_px(CENTER_X - half), _px(y), _px(CENTER_X + half), _px(y)), fill=PAPER_EDGE, width=_px(1.5))
    draw.regular_polygon((_px(CENTER_X), _px(y), _px(5)), 4, fill=PAPER_EDGE)


def _head(draw: ImageDraw.ImageDraw, number: int) -> None:
    _line(draw, 190, SALON, 22, INK_DIM)
    _line(draw, 220, f"№ {number}", 18, INK_DIM)
    _rule(draw, 246)


def _wear(layer: Image.Image) -> None:
    """Неровный оттиск: местами краска легла слабее."""
    noise = Image.effect_noise((max(layer.width // 6, 1), max(layer.height // 6, 1)), 64)
    faded = noise.resize(layer.size, Image.BILINEAR).point(lambda value: 255 if value > 104 else 168)
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), faded))


def _press(picture: Image.Image, layer: Image.Image, center: tuple[float, float], angle: float) -> None:
    _wear(layer)
    layer = layer.rotate(angle, expand=True, resample=Image.BICUBIC)
    picture.paste(layer, (_px(center[0]) - layer.width // 2, _px(center[1]) - layer.height // 2), layer)


def _stamp(picture: Image.Image, center: tuple[float, float], text: str, color: Color, angle: float, size: int = 46) -> None:
    """Прямоугольный штамп с приговором, поставленный чуть криво."""
    font = art.font(size)
    width = round(ImageDraw.Draw(picture).textlength(text, font=font)) + _px(64)
    height = _px(size + 40)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    ink = (*color, 255)
    draw.rounded_rectangle((_px(3), _px(3), width - _px(3), height - _px(3)), radius=_px(12), outline=ink, width=_px(5))
    draw.rounded_rectangle((_px(12), _px(12), width - _px(12), height - _px(12)), radius=_px(6), outline=ink, width=_px(1.5))
    draw.text((width / 2, height / 2), text, font=font, fill=ink, anchor="mm")
    _press(picture, layer, center, angle)


def _seal(picture: Image.Image, center: tuple[float, float], radius: float, angle: float) -> None:
    """Круглая печать салона: надпись по кругу и третий глаз в середине."""
    side = _px(radius * 2 + 16)
    middle = side / 2
    layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    ink = (*SEAL_COLOR, 255)
    for ring, width in ((radius, 4), (radius - 8, 1.5), (radius * 0.55, 1.5)):
        draw.ellipse((middle - _px(ring), middle - _px(ring), middle + _px(ring), middle + _px(ring)), outline=ink, width=_px(width))

    font = art.font(15)
    advances = [draw.textlength(char, font=font) for char in C.PAPER_SEAL]
    total = sum(advances)
    walked = 0.0
    for char, advance in zip(C.PAPER_SEAL, advances):
        turn = 360 * (walked + advance / 2) / total  # по часовой стрелке от верхней точки
        walked += advance
        glyph = Image.new("RGBA", (_px(28), _px(28)), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((glyph.width / 2, glyph.height / 2), char, font=font, fill=ink, anchor="mm")
        glyph = glyph.rotate(-turn, resample=Image.BICUBIC)
        x = middle + math.sin(math.radians(turn)) * _px(radius * 0.77)
        y = middle - math.cos(math.radians(turn)) * _px(radius * 0.77)
        layer.alpha_composite(glyph, (round(x - glyph.width / 2), round(y - glyph.height / 2)))

    eye_w, eye_h = radius * 0.4, radius * 0.19
    lid = [(middle + _px(eye_w * math.cos(t)), middle + _px(eye_h * math.sin(t) * (1 - 0.35 * abs(math.cos(t)))))
           for t in (2 * math.pi * step / 60 for step in range(61))]
    draw.line(lid, fill=ink, width=_px(2), joint="curve")
    draw.ellipse((middle - _px(eye_h * 0.72), middle - _px(eye_h * 0.72), middle + _px(eye_h * 0.72), middle + _px(eye_h * 0.72)), fill=ink)
    for ray in range(-2, 3):
        tilt = math.radians(ray * 24)
        draw.line((middle + math.sin(tilt) * _px(eye_h * 1.5), middle - math.cos(tilt) * _px(eye_h * 1.5),
                   middle + math.sin(tilt) * _px(eye_h * 2.2), middle - math.cos(tilt) * _px(eye_h * 2.2)), fill=ink, width=_px(1.5))
    _press(picture, layer, center, angle)


def _signature(draw: ImageDraw.ImageDraw, left: float, y: float, width: float, rng: random.Random) -> None:
    """Размашистая подпись наставницы и черта под ней."""
    knots = [(left + width * index / 9 + rng.uniform(-6, 6), y - rng.uniform(4, 34) * (1 if index % 2 else -0.5)) for index in range(10)]
    knots.append((left + width * 1.08, y - 20))
    draw.line(art.scaled(art.catmull(knots, 12)), fill=INK_PEN, width=_px(2), joint="curve")
    draw.line((_px(left - 6), _px(y + 26), _px(left + width + 14), _px(y + 26)), fill=INK_DIM, width=_px(1))
    _line(draw, y + 44, "мадам-наставница", 16, INK_DIM, x=left + width / 2, width=round(width + 40))


def _brand(draw: ImageDraw.ImageDraw, brand: str) -> None:
    if brand:
        _line(draw, 1022, art.printable(brand), 18, art.GOLD)


def _name(name: str) -> str:
    return art.printable(name, ANONYMOUS)


def render_certificate(paper: Certificate, brand: str = "") -> bytes:
    """Справка о живом приёме: предсказание мадам и приговор клиента штампом."""
    if paper.outcome not in C.PAPER_STAMPS or paper.confidence not in C.PAPER_CONFIDENCE:
        raise ValueError(f"нечего написать в справке: {paper.outcome}, {paper.confidence}")
    rng = random.Random(f"guru-certificate:{paper.number}")
    image, picture, box = art.canvas(CERTIFICATE_TITLE, rng)
    draw = _sheet(picture)
    _head(draw, paper.number)
    _line(draw, 286, "Дана", 20, INK_DIM)
    _line(draw, 330, _name(paper.client), 44)
    _line(draw, 380, "в том, что мадам", 20, INK_DIM)
    _line(draw, 424, _name(paper.host), 40)
    _line(draw, 472, "предсказала:", 20, INK_DIM)
    # Короткое предсказание пишется крупнее и встаёт посередине: лист не должен выглядеть полупустым.
    size, lines = _paragraph(draw, f"«{paper.prediction}»", 34, max_lines=4)
    spare = WORDS[1] - WORDS[0] - (len(lines) - 1) * size * LEADING - 94
    bottom = _block(draw, WORDS[0] + max(spare, 0) / 2, size, lines)
    _line(draw, bottom + 58, "Уверенность мадам", 20, INK_DIM)
    _line(draw, bottom + 94, C.PAPER_CONFIDENCE[paper.confidence], 28)
    _line(draw, 784, "Приговор клиента", 20, INK_DIM)
    _line(draw, 946, f"Касса мадам: {art.printable(signed_rub(paper.fee))}", 22, INK_DIM)
    _stamp(picture, (CENTER_X, 864), C.PAPER_STAMPS[paper.outcome], STAMP_COLORS[paper.outcome], rng.uniform(4, 8))
    _brand(draw, brand)
    return art.finish(image, picture, box)


def render_diploma(paper: Diploma, brand: str = "") -> bytes:
    """Диплом о смене: звание, деньги, попадания и печать салона."""
    rng = random.Random(f"guru-diploma:{paper.number}")
    image, picture, box = art.canvas(DIPLOMA_TITLE, rng)
    draw = _sheet(picture)
    _head(draw, paper.number)
    _line(draw, 290, "Настоящим удостоверяется, что мадам", 20, INK_DIM)
    _line(draw, 340, _name(paper.player), 46)
    deed = "разоблачена клиентами и получает звание" if paper.exposed else "отработала смену и получает звание"
    _line(draw, 394, deed, 20, INK_DIM)
    size, lines = _paragraph(draw, art.printable(paper.title), 36, max_lines=3)
    bottom = _block(draw, 452, size, lines, INK_TITLE)
    _rule(draw, bottom + 50)
    top = bottom + 88
    figures = (
        ("Очки", str(paper.score)),
        ("За смену", art.printable(signed_rub(paper.money))),
        ("Попаданий", f"{paper.hits} из {paper.clients}"),
    )
    for index, (label, value) in enumerate(figures):
        x = CENTER_X + (index - 1) * 166
        _line(draw, top, label, 18, INK_DIM, x=x, width=150)
        _line(draw, top + 38, value, 30, x=x, width=150)
    y = top + 96
    for achievement in paper.achievements[:2]:
        _line(draw, y, f"• {art.printable(achievement)}", 22)
        y += 34
    _signature(draw, 150, 886, 170, rng)
    _seal(picture, (520, 866), 84, rng.uniform(-16, -6))
    if paper.exposed:
        _stamp(picture, (330, 776), C.PAPER_STAMPS["exposure"], STAMP_COLORS["exposure"], rng.uniform(5, 9), size=38)
    _brand(draw, brand)
    return art.finish(image, picture, box)
