"""Image-generation adapter for the autonomous channel."""

from __future__ import annotations

import asyncio
from pathlib import Path


async def generate_channel_image(prompt_ru: str) -> tuple[bytes | None, str | None]:
    """Generate bytes through the shared GigaChat-first image waterfall."""
    from features.image_generation import generate_image_bytes

    return await generate_image_bytes(
        prompt_ru,
        translate_fallback=True,
        log_context="channel",
    )


async def overlay_channel_text(image_bytes: bytes, text: str) -> bytes | None:
    """Use the exact same text overlay as the public «скаламбурь» command."""
    if not image_bytes or not (text or "").strip():
        return None

    from AI.picgeneration import _overlay_text_on_image

    path = await asyncio.to_thread(_overlay_text_on_image, image_bytes, text)
    if not path:
        return None

    output = Path(path)
    try:
        return await asyncio.to_thread(output.read_bytes)
    finally:
        try:
            await asyncio.to_thread(output.unlink, missing_ok=True)
        except Exception:
            pass
