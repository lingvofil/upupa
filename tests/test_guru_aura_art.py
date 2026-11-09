"""Рисунок ауры: всегда корректный JPEG, слои зависят от выпавших чувств."""

import io
from itertools import combinations

import pytest
from PIL import Image

from games.guru import aura_art
from games.guru import content as C


def picture(marks=()):
    return Image.open(io.BytesIO(aura_art.render(marks))).convert("RGB")


def difference(first, second):
    a, b = first.resize((64, 96)).tobytes(), second.resize((64, 96)).tobytes()
    return sum(abs(x - y) for x, y in zip(a, b))


def test_every_emotion_has_a_layer_and_an_omen():
    assert set(aura_art.LAYERS) == set(C.EMOTIONS)
    assert all(tag.omen for tag in C.EMOTIONS.values())


@pytest.mark.parametrize("marks", [(), *combinations(C.EMOTIONS, 2), *combinations(C.EMOTIONS, 3)], ids=lambda value: "+".join(value) or "none")
def test_every_combination_renders_a_card_sized_jpeg(marks):
    data = aura_art.render(marks)
    assert data[:2] == b"\xff\xd8" and len(data) < 1_000_000
    assert Image.open(io.BytesIO(data)).size == (aura_art.WIDTH, aura_art.HEIGHT)


@pytest.mark.parametrize("emotion", list(C.EMOTIONS))
def test_each_emotion_changes_the_picture(emotion):
    assert difference(picture(), picture((emotion,))) > 1000


def test_order_of_marks_does_not_change_the_layers():
    first, second = picture(("hope", "anger")), picture(("anger", "hope"))
    assert difference(first, second) < difference(picture(("hope", "anger")), picture(("guilt", "fear")))


def test_unknown_emotion_is_refused():
    with pytest.raises(ValueError):
        aura_art.render(("boredom",))
