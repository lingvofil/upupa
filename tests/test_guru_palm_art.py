"""Рисунок ладони: всегда корректный JPEG, а изъяны зависят от выпавших линий."""

import io
from itertools import combinations

import pytest
from PIL import Image

from games.guru import content as C
from games.guru import art, palm_art


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
    assert Image.open(io.BytesIO(data)).size == (art.WIDTH, art.HEIGHT)


@pytest.mark.parametrize("sphere", list(C.SPHERES))
def test_each_defect_changes_the_picture(sphere):
    assert difference(picture(), picture((sphere,))) > 1000


def test_same_lines_give_the_same_shape_of_defect():
    first, second = picture(("love", "work")), picture(("work", "love"))
    assert difference(first, second) < difference(picture(("love", "work")), picture(("money", "family")))


def test_unknown_line_is_refused():
    with pytest.raises(ValueError):
        palm_art.render(("luck",))


@pytest.mark.parametrize("hand", sorted(palm_art.HANDS))
def test_every_hand_type_renders_and_looks_different(hand):
    data = palm_art.render(("love",), hand)
    assert data[:2] == b"\xff\xd8" and Image.open(io.BytesIO(data)).size == (palm_art.WIDTH if hasattr(palm_art, "WIDTH") else 738, 1100)
    if hand != palm_art.PLAIN_HAND:
        plain = Image.open(io.BytesIO(palm_art.render(("love",)))).convert("L").resize((64, 96)).tobytes()
        other = Image.open(io.BytesIO(data)).convert("L").resize((64, 96)).tobytes()
        assert sum(abs(a - b) for a, b in zip(plain, other)) > 20000, f"рука {hand} неотличима от обычной"


def test_unknown_hand_is_refused():
    with pytest.raises(ValueError):
        palm_art.render((), "paw")


@pytest.mark.parametrize("areas", [(), ("heart",), ("heart", "head"), ("head", "thumb"), ("heart", "thumb")], ids=lambda value: "+".join(value) or "none")
def test_study_palm_renders_for_every_inspection(areas):
    data = palm_art.render_study(areas)
    assert data[:2] == b"\xff\xd8" and len(data) < 1_000_000


def test_study_palm_refuses_unknown_areas():
    with pytest.raises(ValueError):
        palm_art.render_study(("elbow",))


@pytest.mark.parametrize("hand", C.LIVE_HANDS)
def test_study_keeps_the_clients_hand_shape_and_highlights_inspected_areas(hand):
    plain = Image.open(io.BytesIO(palm_art.render_study((), hand))).convert("RGB")
    marked = Image.open(io.BytesIO(palm_art.render_study(("heart", "thumb"), hand))).convert("RGB")
    other = Image.open(io.BytesIO(palm_art.render_study(("heart", "thumb")))).convert("RGB")
    assert plain.size == marked.size == other.size == (art.WIDTH, art.HEIGHT)
    assert difference(plain, marked) > 1000
    assert difference(marked, other) > 1000


def test_study_refuses_unknown_hand_shape():
    with pytest.raises(ValueError):
        palm_art.render_study((), "paw")
