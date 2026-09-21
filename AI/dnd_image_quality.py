"""DnD-specific image delivery that preserves the original scene prompt."""
from __future__ import annotations

import logging

from aiogram.types import BufferedInputFile


async def generate_dnd_image_bytes(prompt: str, *, should_continue=None):
    """Use the DnD waterfall without translating/compressing scene prompts."""
    from features.image_generation import generate_image_bytes

    return await generate_image_bytes(
        prompt,
        log_context="dnd",
        should_continue=should_continue,
    )


def install_dnd_image_quality(dnd) -> None:
    from AI import dnd_campaign as campaign

    if getattr(campaign, "_upupa_dnd_image_quality_installed", False):
        return

    async def image(bot, chat_id, prompt, filename, caption, *, deliver_if=None):
        try:
            if deliver_if is not None and not deliver_if():
                logging.info("[dnd] stale image skipped before generation chat_id=%s", chat_id)
                return None
            data, provider = await generate_dnd_image_bytes(
                prompt,
                should_continue=deliver_if,
            )
            if not data:
                logging.warning("[dnd] all image providers failed chat_id=%s", chat_id)
                return None
            if deliver_if is not None and not deliver_if():
                logging.info(
                    "[dnd] stale image dropped after generation chat_id=%s provider=%s",
                    chat_id,
                    provider,
                )
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
