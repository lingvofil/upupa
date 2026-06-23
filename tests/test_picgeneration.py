"""Юнит-тесты подбора бесплатных image-моделей из каталога Pollinations."""
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
