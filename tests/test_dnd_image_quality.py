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


def test_dnd_waterfall_uses_kandinsky_before_huggingface(monkeypatch):
    from AI import aihorde_image, gigachat_image, picgeneration as pg
    from features import image_generation

    calls = []

    async def no_gigachat(prompt):
        calls.append(("gigachat", prompt))
        return None

    async def no_horde(prompt):
        calls.append(("aihorde", prompt))
        return None

    async def no_pollinations(prompt):
        calls.append(("pollinations", prompt))
        return None

    async def kandinsky(prompt):
        calls.append(("kandinsky", prompt))
        return b"kandinsky-image"

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("provider after Kandinsky must not run")

    monkeypatch.setattr(gigachat_image, "generate_gigachat_image", no_gigachat)
    monkeypatch.setattr(aihorde_image, "generate_aihorde_image", no_horde)
    monkeypatch.setattr(pg, "pollinations_generate", no_pollinations)
    monkeypatch.setattr(pg, "kandinsky_generate", kandinsky)
    monkeypatch.setattr(pg, "hf_generate", must_not_run)
    monkeypatch.setattr(pg, "cf_generate_t2i", must_not_run)

    result = asyncio.run(
        image_generation.generate_image_bytes(
            "exact DnD prompt",
            log_context="dnd",
        )
    )

    assert result == (b"kandinsky-image", "kandinsky")
    assert calls == [
        ("gigachat", "exact DnD prompt"),
        ("aihorde", "exact DnD prompt"),
        ("pollinations", "exact DnD prompt"),
        ("kandinsky", "exact DnD prompt"),
    ]


def test_dnd_waterfall_never_uses_cloudflare(monkeypatch):
    from AI import aihorde_image, gigachat_image, picgeneration as pg
    from features import image_generation

    cloudflare_calls = []

    async def none(*_args, **_kwargs):
        return None

    async def cloudflare(prompt):
        cloudflare_calls.append(prompt)
        return b"cloudflare-image"

    monkeypatch.setattr(gigachat_image, "generate_gigachat_image", none)
    monkeypatch.setattr(aihorde_image, "generate_aihorde_image", none)
    monkeypatch.setattr(pg, "pollinations_generate", none)
    monkeypatch.setattr(pg, "kandinsky_generate", none)
    monkeypatch.setattr(pg, "hf_generate", none)
    monkeypatch.setattr(pg, "cf_generate_t2i", cloudflare)

    result = asyncio.run(
        image_generation.generate_image_bytes(
            "exact DnD prompt",
            log_context="dnd",
        )
    )

    assert result == (None, None)
    assert cloudflare_calls == []
