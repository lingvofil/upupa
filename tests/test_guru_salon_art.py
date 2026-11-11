"""Рисунок салона: корректный JPEG, каждая покупка, подарок и клиент видны на картинке."""

import io

import pytest
from PIL import Image

from games.guru import content as C
from games.guru import salon, salon_art


def picture(items=(), client=None, gifts=()):
    return Image.open(io.BytesIO(salon_art.render(items, client, gifts))).convert("RGB")


def difference(first, second):
    """Насколько картинки разные. Мелкие отличия не считаем: поверх рисунка ложится случайное зерно."""
    a, b = first.resize((164, 244)).tobytes(), second.resize((164, 244)).tobytes()
    return sum(delta for delta in (abs(x - y) for x, y in zip(a, b)) if delta > 16)


@pytest.fixture(scope="module")
def empty():
    return picture()


def test_every_client_type_and_gift_can_be_drawn():
    assert set(salon_art.CLIENTS) == {*C.ARCHETYPES, salon.GUEST}
    assert set(salon_art._GIFTS) == set(salon.GIFTS) == set(C.ARCHETYPES), "подарок есть у каждого типа клиента"


def test_salon_is_a_card_sized_jpeg(empty):
    data = salon_art.render(tuple(salon.ITEMS), "tycoon", tuple(salon.GIFTS))
    assert data[:2] == b"\xff\xd8" and len(data) < 1_000_000
    assert Image.open(io.BytesIO(data)).size == empty.size == (salon_art.WIDTH, salon_art.HEIGHT)


@pytest.mark.parametrize("key", list(salon.ITEMS))
def test_every_purchase_is_visible(empty, key):
    # Сравниваем с салоном того же звания: иначе разницу дали бы одни обои.
    rank_mate = next(other for other in salon.ITEMS if other != key)
    assert difference(picture((key,)), picture((rank_mate,))) > 20000, f"{key} не видно в салоне"


@pytest.mark.parametrize("key", list(salon.GIFTS))
def test_every_gift_stands_on_the_shelf(empty, key):
    assert difference(picture(gifts=(key,)), empty) > 5000


@pytest.mark.parametrize("client", sorted(salon_art.CLIENTS))
def test_every_client_sits_at_the_table(empty, client):
    assert difference(picture(client=client), empty) > 100000


def test_clients_look_different():
    looks = {client: picture(client=client) for client in ("skeptic", "granny", "blogger", salon.GUEST)}
    keys = list(looks)
    for index, first in enumerate(keys):
        for second in keys[index + 1:]:
            assert difference(looks[first], looks[second]) > 30000, f"{first} и {second} на одно лицо"


def test_walls_follow_the_rank_of_the_salon():
    keys = list(salon.ITEMS)
    assert salon_art.title(()) == "КАМОРКА ЗА ГАРАЖАМИ" and salon_art.title(keys) == "ХРАМ ВСЕХ РЕЛИГИЙ СРАЗУ"
    assert len({salon_art._tier(count) for count in (0, 1, 4, 8, 12)}) == 5


def test_picture_does_not_depend_on_the_order_of_keys():
    first = picture(("tea", "cat", "neon"), "granny", ("student", "granny"))
    assert difference(first, picture(("neon", "tea", "cat"), "granny", ("granny", "student"))) < 1000


def test_stale_keys_are_ignored_but_unknown_client_is_refused():
    assert difference(picture(("tea", "samovar"), None, ("ghost",)), picture(("tea",))) < 1000
    with pytest.raises(ValueError):
        salon_art.render((), "ghost")
