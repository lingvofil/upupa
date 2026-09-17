"""Tests for the DnD image adapter."""

import asyncio

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)
from AI import dnd_image_quality


def test_dnd_uses_shared_waterfall_with_original_prompt(monkeypatch):
    import features.image_generation as image_generation

    calls = []

    async def fake_generate(prompt, *, translate_fallback=True, log_context="image"):
        calls.append((prompt, translate_fallback, log_context))
        return b"dnd-image", "aihorde"

    monkeypatch.setattr(image_generation, "generate_image_bytes", fake_generate)

    prompt = "Long DnD scene prompt with exact character details"
    result = asyncio.run(dnd_image_quality.generate_dnd_image_bytes(prompt))

    assert result == (b"dnd-image", "aihorde")
    assert calls == [(prompt, True, "dnd")]
