"""Справка и диплом: всегда корректный JPEG, имена из мессенджера не ломают рисунок."""

import io

import pytest
from PIL import Image

from games.guru import art, cert_art, rules
from games.guru import content as C
from games.guru.contract import Certificate, Diploma

BRAND = "@salon_bot · напиши «гуру»"
BRAND_BAND = (60, 1008, 678, 1036)


def certificate(**changes):
    values = dict(number=4821, host="Зоя", client="Гоша", prediction="До конца недели налоговая потеряет твои документы.",
                  confidence="see", outcome="hit", fee=1500)
    return Certificate(**{**values, **changes})


def diploma(**changes):
    values = dict(number=7310, player="Зоя", title=C.SCORE_TITLES[0][1], score=14, money=6500, hits=3, clients=3,
                  achievements=(C.MASTERY_ACHIEVEMENT, C.STREAK_ACHIEVEMENT), exposed=False)
    return Diploma(**{**values, **changes})


def opened(data):
    assert data[:2] == b"\xff\xd8" and len(data) < 1_000_000
    image = Image.open(io.BytesIO(data))
    assert image.size == (art.WIDTH, art.HEIGHT)
    return image.convert("RGB")


def difference(first, second, box):
    a, b = first.crop(box).resize((64, 64)).tobytes(), second.crop(box).resize((64, 64)).tobytes()
    return sum(abs(x - y) for x, y in zip(a, b))


def gold(image, box):
    return sum(sum(abs(value - wanted) for value, wanted in zip(pixel, art.GOLD)) < 90 for pixel in image.crop(box).getdata())


@pytest.mark.parametrize("outcome", list(C.PAPER_STAMPS))
@pytest.mark.parametrize("confidence", list(C.PAPER_CONFIDENCE))
def test_every_certificate_renders(outcome, confidence):
    opened(cert_art.render_certificate(certificate(outcome=outcome, confidence=confidence, fee=-1500), BRAND))


def test_papers_cover_every_verdict_and_confidence():
    assert set(C.PAPER_STAMPS) == set(cert_art.STAMP_COLORS) == {"hit", "partial", "miss", "exposure"}
    assert set(C.PAPER_CONFIDENCE) == set(C.CONFIDENCE_PREFIX)
    for text in (*C.PAPER_STAMPS.values(), *C.PAPER_CONFIDENCE.values(), C.PAPER_SEAL):
        assert art.printable(text) == " ".join(text.split()), "подписи на бумаге только из знаков шрифта"


def test_stamp_depends_on_the_verdict():
    stamp = (120, 800, 620, 930)
    hit, exposure = (opened(cert_art.render_certificate(certificate(outcome=outcome))) for outcome in ("hit", "exposure"))
    assert difference(hit, exposure, stamp) > 20_000


@pytest.mark.parametrize("field, value", [("outcome", "win"), ("confidence", "sure")])
def test_unknown_verdict_is_an_error(field, value):
    with pytest.raises(ValueError):
        cert_art.render_certificate(certificate(**{field: value}))


@pytest.mark.parametrize("paper", [
    diploma(),
    diploma(title=C.LOW_SCORE_TITLE, score=2, money=500, hits=1, achievements=()),
    diploma(title=C.CROOK_TITLE, score=1, money=9000, hits=1, clients=4, achievements=(C.STREAK_ACHIEVEMENT,)),
    diploma(title=C.EXPOSED_TITLE, score=-3, money=-2500, hits=0, clients=2, achievements=(), exposed=True),
], ids=["top", "low", "crook", "exposed"])
def test_every_diploma_renders(paper):
    opened(cert_art.render_diploma(paper, BRAND))


def test_long_and_strange_names_do_not_break_the_paper():
    longest = max(
        (rules.prediction_plain({"who": who, "what": what, "when": when}) for who in C.WHO for what in C.WHAT for when in C.WHEN),
        key=len,
    )
    opened(cert_art.render_certificate(certificate(host="Очень Длинное Имя Мадам Ая", client="🔥🔥🔥", prediction=longest)))
    opened(cert_art.render_diploma(diploma(player="北京 ✦ 🔥", title=C.EXPOSED_TITLE, exposed=True, achievements=())))


@pytest.mark.parametrize("render, paper", [(cert_art.render_certificate, certificate()), (cert_art.render_diploma, diploma())],
                         ids=["certificate", "diploma"])
def test_brand_is_written_under_the_paper(render, paper):
    plain, branded = opened(render(paper)), opened(render(paper, BRAND))
    assert gold(branded, BRAND_BAND) > gold(plain, BRAND_BAND) + 150, "по подписи переславшие находят игру"


def test_printable_keeps_only_what_the_font_can_draw():
    assert art.printable("🔥Маша🔥  Петрова ★") == "Маша Петрова"
    assert art.printable("Zoe-2 «ё» №1 ₽") == "Zoe-2 «ё» №1 ₽"
    assert art.printable("🔥🔥", "Аноним") == "Аноним" and art.printable("", "Аноним") == "Аноним"
    assert art.printable("Аня\nи\tКот") == "Аня и Кот"
