"""Image-generation adapter for the social graph without Telegram side effects."""

from __future__ import annotations

import logging


async def generate_social_graph_image(prompt_ru: str) -> tuple[bytes | None, str | None]:
    """Generate a social-graph illustration through the existing image waterfall."""
    from AI import picgeneration as pg
    from AI.gigachat_image import generate_gigachat_image

    try:
        image = await generate_gigachat_image(prompt_ru)
        if image:
            return image, "gigachat"

        prompt_en = await pg.translate_to_en(prompt_ru)

        image = await pg.pollinations_generate(prompt_en)
        if image:
            return image, "pollinations"

        image = await pg.hf_generate(prompt_en, "black-forest-labs/FLUX.1-schnell")
        if image:
            return image, "huggingface"

        image = await pg.cf_generate_t2i(prompt_en)
        if image:
            return image, "cloudflare"
    except Exception as exc:
        logging.warning("[social_graph] image generation waterfall failed: %s", exc, exc_info=True)

    return None, None
