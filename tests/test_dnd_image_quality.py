"""Tests for the DnD image adapter."""

import asyncio

from tests import test_smoke_imports  # noqa: F401  (env + heavy-library mocks)
from AI import dnd_image_quality


def test_dnd_uses_shared_waterfall_with_original_prompt(monkeypatch):
    import features.image_generation as image_generation

    calls = []

    async def fake_generate(
        prompt,
        *,
        translate_fallback=True,
        log_context="image",
        should_continue=None,
    ):
        calls.append((prompt, translate_fallback, log_context, should_continue))
        return b"dnd-image", "aihorde"

    monkeypatch.setattr(image_generation, "generate_image_bytes", fake_generate)

    prompt = "Long DnD scene prompt with exact character details"
    result = asyncio.run(dnd_image_quality.generate_dnd_image_bytes(prompt))

    assert result == (b"dnd-image", "aihorde")
    assert calls == [(prompt, True, "dnd", None)]


def test_dnd_waterfall_tries_quick_reserves_before_horde(monkeypatch):
    from AI import aihorde_image, gigachat_image, picgeneration as pg
    from features import image_generation

    calls = []

    async def no_gigachat(prompt):
        calls.append(("gigachat", prompt))
        return None

    async def no_pollinations(prompt):
        calls.append(("pollinations", prompt))
        return None

    async def kandinsky(prompt):
        calls.append(("kandinsky", prompt))
        return b"kandinsky-image"

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("slow provider after Kandinsky must not run")

    monkeypatch.setattr(gigachat_image, "generate_gigachat_image", no_gigachat)
    monkeypatch.setattr(pg, "pollinations_generate", no_pollinations)
    monkeypatch.setattr(pg, "kandinsky_generate", kandinsky)
    monkeypatch.setattr(pg, "hf_generate", must_not_run)
    monkeypatch.setattr(aihorde_image, "generate_aihorde_image", must_not_run)
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
        ("pollinations", "exact DnD prompt"),
        ("kandinsky", "exact DnD prompt"),
    ]


def test_dnd_waterfall_keeps_aihorde_as_last_long_reserve(monkeypatch):
    from AI import aihorde_image, gigachat_image, picgeneration as pg
    from features import image_generation

    calls = []

    async def no_gigachat(prompt):
        calls.append("gigachat")
        return None

    async def no_pollinations(prompt):
        calls.append("pollinations")
        return None

    async def no_kandinsky(prompt):
        calls.append("kandinsky")
        return None

    async def no_hf(prompt, model):
        calls.append(("huggingface", model))
        return None

    async def horde(prompt, *, should_continue=None):
        calls.append(("aihorde", should_continue))
        return b"horde-image"

    monkeypatch.setattr(gigachat_image, "generate_gigachat_image", no_gigachat)
    monkeypatch.setattr(pg, "pollinations_generate", no_pollinations)
    monkeypatch.setattr(pg, "kandinsky_generate", no_kandinsky)
    monkeypatch.setattr(pg, "hf_generate", no_hf)
    monkeypatch.setattr(aihorde_image, "generate_aihorde_image", horde)

    guard = lambda: True
    result = asyncio.run(
        image_generation.generate_image_bytes(
            "exact DnD prompt",
            log_context="dnd",
            should_continue=guard,
        )
    )

    assert result == (b"horde-image", "aihorde")
    assert calls[:4] == [
        "gigachat",
        "pollinations",
        "kandinsky",
        ("huggingface", "black-forest-labs/FLUX.1-schnell"),
    ]
    assert calls[4] == ("aihorde", guard)


def test_dnd_image_providers_use_background_ai_lane(monkeypatch):
    from AI import gigachat_image
    from features import image_generation
    from infrastructure.ai import execution

    lanes = []

    async def gigachat(prompt):
        lanes.append(execution._CURRENT_AI_LANE.get())
        return b"image"

    monkeypatch.setattr(gigachat_image, "generate_gigachat_image", gigachat)

    result = asyncio.run(
        image_generation.generate_image_bytes(
            "scene",
            log_context="dnd",
        )
    )

    assert result == (b"image", "gigachat")
    assert lanes == ["background"]


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


def test_scene_image_prompt_forbids_duplicate_player_depictions():
    from types import SimpleNamespace

    from AI import dnd_campaign

    session = SimpleNamespace(
        participants={
            "1": {"user_id": 1, "name": "Детектор"},
            "2": {"user_id": 2, "name": "ИМакс"},
        },
        character_profiles={
            "1": {
                "style": "герой в панцире",
                "strength": "упрямый",
                "weakness": "лезет первым",
                "special": "вертуха",
            },
            "2": {
                "style": "герой в банке",
                "strength": "скользкий",
                "weakness": "застревает",
                "special": "масляный побег",
            },
        },
    )

    prompt = dnd_campaign._scene_image_prompt(
        session,
        "Детектор вытаскивает ИМакса из банки.",
        style="cinematic fantasy illustration",
    ).casefold()

    assert "one unique person" in prompt
    assert "appear at most once" in prompt
    assert "exactly one depiction of each involved player" in prompt
    assert "never add a second copy" in prompt


def test_scene_illustration_becomes_stale_after_story_advances(monkeypatch):
    from types import SimpleNamespace

    from AI import dnd_campaign

    session = SimpleNamespace(
        chat_id=-100999,
        scene_count=3,
        next_illustration_at=3,
    )
    queued = []
    checks = []

    async def fake_image(
        _bot,
        _chat_id,
        _prompt,
        _filename,
        _caption,
        *,
        deliver_if=None,
    ):
        checks.append(deliver_if())
        return None

    fake_dnd = SimpleNamespace(
        dnd_sessions={session.chat_id: session},
        _start_background_task=lambda coro, *, name: queued.append((coro, name)),
    )
    monkeypatch.setattr(dnd_campaign, "_scene_image_prompt", lambda *_args, **_kwargs: "prompt")
    monkeypatch.setattr(dnd_campaign, "_image", fake_image)

    dnd_campaign._maybe_image(fake_dnd, object(), session, "scene three")

    assert len(queued) == 1
    assert queued[0][1] == "dnd-illustration:-100999:3"

    session.scene_count = 4
    asyncio.run(queued[0][0])

    assert checks == [False]
