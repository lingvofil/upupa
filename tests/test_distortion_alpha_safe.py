from types import SimpleNamespace

import numpy as np
from PIL import Image

from tests import test_smoke_imports

SMOKE_IMPORT_FIXTURES = test_smoke_imports


def _center_seams(gray, num_seams, energy_mode, aux_energy):
    """Deterministic seam map that deliberately cuts through the visible object."""
    height, width = gray.shape
    seams = np.zeros((height, width), dtype=bool)
    start = max(0, (width - num_seams) // 2)
    seams[:, start:start + num_seams] = True
    return seams


def test_pinned_seam_carving_exposes_shared_seam_map_api():
    from services import distortion_alpha_safe as alpha_safe

    assert alpha_safe.SEAM_MAP_AVAILABLE is True
    assert callable(alpha_safe._sc_get_seams)


def test_shared_seam_map_keeps_visible_pixels_coloured(monkeypatch):
    from services import distortion_alpha_safe as alpha_safe

    calls = []

    def fake_get_seams(gray, num_seams, energy_mode, aux_energy):
        calls.append((gray.shape, num_seams, energy_mode))
        return _center_seams(gray, num_seams, energy_mode, aux_energy)

    monkeypatch.setattr(alpha_safe, "_sc_get_seams", fake_get_seams)
    monkeypatch.setattr(alpha_safe, "SEAM_MAP_AVAILABLE", True)

    # Transparent pixels intentionally contain black RGB, exactly like the
    # problematic video stickers.  The visible object has a semi-transparent
    # anti-aliased border so interpolation can exercise halo handling.
    source = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    pixels = source.load()
    for y in range(7, 33):
        for x in range(8, 32):
            border = x in (8, 31) or y in (7, 32)
            pixels[x, y] = (240, 45, 30, 120 if border else 255)

    result = alpha_safe._seam_resize_rgba_same_map(source, 24, 26)
    rgba = np.asarray(result, dtype=np.uint8)
    alpha = rgba[:, :, 3]
    rgb = rgba[:, :, :3]

    visible = alpha > 32
    assert visible.any()

    visible_rgb = rgb[visible].astype(np.int16)
    # A black silhouette would put many visible pixels near RGB=(0,0,0).
    # With shared seams + premultiplied resampling the red object stays red.
    assert np.percentile(visible_rgb[:, 0], 5) > 120
    assert np.percentile(visible_rgb.sum(axis=1), 5) > 150

    # Exactly one seam map per axis: colour and alpha are never carved in
    # separate passes, which was the source of the spatial desynchronisation.
    assert len(calls) == 2
    assert calls[0] == ((40, 40), 16, "backward")
    assert calls[1] == ((24, 40), 14, "backward")


def test_fully_transparent_pixels_have_clean_hidden_rgb(monkeypatch):
    from services import distortion_alpha_safe as alpha_safe

    monkeypatch.setattr(alpha_safe, "_sc_get_seams", _center_seams)
    monkeypatch.setattr(alpha_safe, "SEAM_MAP_AVAILABLE", True)

    source = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    for y in range(8, 24):
        for x in range(8, 24):
            source.putpixel((x, y), (80, 190, 240, 255))

    result = alpha_safe._seam_resize_rgba_same_map(source, 22, 22)
    rgba = np.asarray(result, dtype=np.uint8)
    transparent = rgba[:, :, 3] <= 1

    assert transparent.any()
    assert np.all(rgba[:, :, :3][transparent] == 0)


def test_installer_replaces_only_rgba_distortion_functions():
    from services import distortion_alpha_safe as alpha_safe

    original_static = object()
    original_video = object()
    module = SimpleNamespace(
        apply_rgba_static_sticker_distortion=original_static,
        apply_rgba_video_sticker_distortion=original_video,
        _alpha_safe_rgba_installed=False,
    )

    alpha_safe.install_alpha_safe_rgba(module)

    assert module.apply_rgba_static_sticker_distortion is alpha_safe.apply_rgba_static_sticker_distortion
    assert module.apply_rgba_video_sticker_distortion is alpha_safe.apply_rgba_video_sticker_distortion
    assert module._alpha_safe_rgba_installed is True
