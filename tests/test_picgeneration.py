"""Юнит-тесты подбора бесплатных image-моделей из каталога Pollinations."""
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from tests import test_smoke_imports  # noqa: F401  (env + моки)

from AI import picgeneration as pg


def test_is_free_by_price():
    assert pg._is_free({"name": "flux", "price": 0})
    assert not pg._is_free({"name": "seedream5", "price": 0.99})
    assert pg._is_free({"name": "x", "cost": 0.0})


def test_is_free_by_flag_and_tier():
    assert pg._is_free({"name": "a", "free": True})
    assert pg._is_free({"name": "b", "paid": False})
    assert pg._is_free({"name": "c", "tier": "anonymous"})
    assert not pg._is_free({"name": "d", "tier": "flower"})


def test_extract_free_skips_video_and_paid():
    catalog = [
        {"name": "flux", "price": 0},
        {"name": "seedream5", "price": 0.99},          # платная — мимо
        {"name": "veo", "price": 0, "type": "video"},   # видео — мимо
        {"name": "zimage", "tier": "anonymous"},
        "мусор",
    ]
    out = pg._extract_free_image_models(catalog)
    assert "flux" in out and "zimage" in out
    assert "seedream5" not in out and "veo" not in out


def test_order_prefers_flux_first():
    out = pg._order_image_models(["zimage", "neizvestnaya", "flux"])
    assert out[0] == "flux"
    assert out[-1] == "neizvestnaya"


def test_fallback_queue_when_empty():
    assert pg._extract_free_image_models("мусор") == []
    assert pg._IMAGE_FALLBACK_QUEUE[0] == "flux"


def test_move_nvidia_comparison_labels_to_bottom():
    image = Image.new("RGB", (800, 400), (90, 100, 110))
    draw = ImageDraw.Draw(image)
    draw.rectangle((90, 280, 310, 340), fill=(0, 0, 0))
    draw.rectangle((500, 275, 720, 335), fill=(255, 255, 255))
    draw.rectangle((500, 335, 720, 340), fill=(118, 185, 0))

    buffer = BytesIO()
    image.save(buffer, format="PNG")

    result = Image.open(BytesIO(pg.move_nvidia_comparison_labels_to_bottom(buffer.getvalue()))).convert("RGB")

    assert pg._find_nvidia_label_box(result, 0, 400, "dark")[3] == 400
    assert pg._find_nvidia_label_box(result, 400, 800, "light")[3] == 400


@pytest.mark.anyio
async def test_pollinations_stops_on_payment_required(monkeypatch):
    calls = []

    async def fake_queue():
        return ["flux", "zimage"]

    class Response:
        status_code = 402
        content = b'{"error":{"code":"PAYMENT_REQUIRED"}}'

    def fake_get(*args, **kwargs):
        calls.append(args)
        return Response()

    monkeypatch.setattr(pg, "get_free_image_model_queue", fake_queue)
    monkeypatch.setattr(pg.requests, "get", fake_get)

    assert await pg.pollinations_generate("bike") is None
    assert len(calls) == 1
