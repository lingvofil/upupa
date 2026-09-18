"""Shared image-generation waterfall without Telegram side effects."""

from __future__ import annotations

import logging


async def generate_image_bytes(
    prompt: str,
    *,
    translate_fallback: bool = True,
    log_context: str = "image",
) -> tuple[bytes | None, str | None]:
    """Generate image bytes through the project's standard image waterfall.

    GigaChat receives the original prompt directly. If it fails, AI Horde is
    the first independent reserve, followed by Pollinations, Hugging Face and
    Cloudflare. Providers after GigaChat receive the translated/enhanced prompt
    when ``translate_fallback`` is true. DnD prompts are already deliberately
    written for image generation, so they must never be compressed by
    translate_to_en().
    """
    from AI import picgeneration as pg
    from AI.aihorde_image import generate_aihorde_image
    from AI.gigachat_image import generate_gigachat_image

    if log_context == "dnd":
        translate_fallback = False

    try:
        image = await generate_gigachat_image(prompt)
        if image:
            logging.info("[%s] image provider=gigachat", log_context)
            return image, "gigachat"

        fallback_prompt = await pg.translate_to_en(prompt) if translate_fallback else prompt

        image = await generate_aihorde_image(fallback_prompt)
        if image:
            logging.info("[%s] image provider=aihorde", log_context)
            return image, "aihorde"

        image = await pg.pollinations_generate(fallback_prompt)
        if image:
            logging.info("[%s] image provider=pollinations", log_context)
            return image, "pollinations"

        image = await pg.hf_generate(fallback_prompt, "black-forest-labs/FLUX.1-schnell")
        if image:
            logging.info("[%s] image provider=huggingface", log_context)
            return image, "huggingface"

        image = await pg.cf_generate_t2i(fallback_prompt)
        if image:
            logging.info("[%s] image provider=cloudflare", log_context)
            return image, "cloudflare"
    except Exception as exc:
        logging.warning("[%s] image generation waterfall failed: %s", log_context, exc, exc_info=True)

    return None, None
