"""Рисунок ладони: всегда корректный JPEG, а изъяны зависят от выпавших линий."""

import io
from itertools import combinations

import pytest
from PIL import Image

from games.guru import content as C
from games.guru import palm_art


def picture(lines=()):
    return Image.open(io.BytesIO(palm_art.render(lines))).convert("RGB")


def difference(first, second):
    a, b = first.resize((64, 96)).tobytes(), second.resize((64, 96)).tobytes()
    return sum(abs(x - y) for x, y in zip(a, b))


def test_every_sphere_has_a_line():
    assert set(palm_art.LINES) == set(C.SPHERES)


@pytest.mark.parametrize("lines", [(), *combinations(C.SPHERES, 2), *combinations(C.SPHERES, 3)], ids=lambda value: "+".join(value) or "none")
def test_every_combination_renders_a_card_sized_jpeg(lines):
    data = palm_art.render(lines)
    assert data[:2] == b"\xff\xd8" and len(data) < 1_000_000
    assert Image.open(io.BytesIO(data)).size == (palm_art.WIDTH, palm_art.HEIGHT)


@pytest.mark.parametrize("sphere", list(C.SPHERES))
def test_each_defect_changes_the_picture(sphere):
    assert difference(picture(), picture((sphere,))) > 1000


def test_same_lines_give_the_same_shape_of_defect():
    first, second = picture(("love", "work")), picture(("work", "love"))
    assert difference(first, second) < difference(picture(("love", "work")), picture(("money", "family")))


def test_unknown_line_is_refused():
    with pytest.raises(ValueError):
        palm_art.render(("luck",))
