"""Regression tests for comic panel image generation."""

import asyncio

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)
from AI import comic


def test_comic_panel_uses_gigachat_text_to_image(monkeypatch):
    calls = []

    async def fake_generate(prompt: str):
        calls.append(prompt)
        return b"gigachat-comic-panel"

    monkeypatch.setattr(comic, "generate_gigachat_image", fake_generate)

    result = asyncio.run(comic._generate_panel_image("A surprised bird opens a fridge"))

    assert result == b"gigachat-comic-panel"
    assert calls == [
        f"{comic.PANEL_STYLE}, A surprised bird opens a fridge",
    ]
