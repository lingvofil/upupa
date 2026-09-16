"""DnD-specific image path that strongly prefers GigaChat and preserves prompts."""
from __future__ import annotations

import asyncio
import logging

from aiogram.types import BufferedInputFile


DND_GIGACHAT_IMAGE_ATTEMPTS = 2
DND_GIGACHAT_IMAGE_RETRY_DELAY_SECONDS = 1.0


async def generate_dnd_image_bytes(prompt: str):
    """Try GigaChat repeatedly, then fall back without rewriting the DnD prompt."""
    from AI.gigachat_image import generate_gigachat_image

    for attempt in range(1, DND_GIGACHAT_IMAGE_ATTEMPTS + 1):
        data = await generate_gigachat_image(prompt)
        if data:
            logging.info("[dnd] image provider=gigachat attempt=%s", attempt)
            return data, "gigachat"
        logging.warning("[dnd] GigaChat image attempt %s/%s returned no image", attempt, DND_GIGACHAT_IMAGE_ATTEMPTS)
        if attempt < DND_GIGACHAT_IMAGE_ATTEMPTS:
            await asyncio.sleep(DND_GIGACHAT_IMAGE_RETRY_DELAY_SECONDS)

    # The shared waterfall starts with GigaChat once more and only then goes to
    # reserves. Crucially, preserve the original long DnD prompt: its composition
    # and negative constraints must not be compressed by translate_to_en().
    from features.image_generation import generate_image_bytes

    return await generate_image_bytes(
        prompt,
        translate_fallback=False,
        log_context="dnd-fallback",
    )


def install_dnd_image_quality(dnd) -> None:
    from AI import dnd_campaign as campaign

    if getattr(campaign, "_upupa_dnd_image_quality_installed", False):
        return

    async def image(bot, chat_id, prompt, filename, caption):
        try:
            data, provider = await generate_dnd_image_bytes(prompt)
            if not data:
                logging.warning("[dnd] all image providers failed chat_id=%s", chat_id)
                return None
            logging.info("[dnd] image delivered provider=%s chat_id=%s", provider, chat_id)
            return await bot.send_photo(
                chat_id,
                BufferedInputFile(data, filename=filename),
                caption=caption,
            )
        except Exception:
            logging.exception("[dnd] image generation failed chat_id=%s", chat_id)
            return None

    campaign._image = image
    campaign._upupa_dnd_image_quality_installed = True


__all__ = ["generate_dnd_image_bytes", "install_dnd_image_quality"]
