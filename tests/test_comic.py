"""Regression tests for comic panel image generation."""

import asyncio

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)
from AI import comic


def test_comic_panel_uses_shared_image_waterfall(monkeypatch):
    import features.image_generation as image_generation

    calls = []

    async def fake_generate(prompt: str, *, translate_fallback=True, log_context="image"):
        calls.append((prompt, translate_fallback, log_context))
        return b"comic-panel", "aihorde"

    monkeypatch.setattr(image_generation, "generate_image_bytes", fake_generate)

    result = asyncio.run(comic._generate_panel_image("A surprised bird opens a fridge"))

    assert result == b"comic-panel"
    assert calls == [
        (
            f"{comic.PANEL_STYLE}, A surprised bird opens a fridge",
            False,
            "comic",
        )
    ]
