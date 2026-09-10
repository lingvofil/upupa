"""Image-generation adapter for the autonomous channel."""

from __future__ import annotations


async def generate_channel_image(prompt_ru: str) -> tuple[bytes | None, str | None]:
    """Generate bytes through the shared GigaChat-first image waterfall."""
    from features.image_generation import generate_image_bytes

    return await generate_image_bytes(
        prompt_ru,
        translate_fallback=True,
        log_context="channel",
    )
