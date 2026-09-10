"""Image-generation adapter for the social graph without Telegram side effects."""

from __future__ import annotations

import logging


async def generate_social_graph_image(prompt: str) -> tuple[bytes | None, str | None]:
    """Generate the anonymous portrait sheet through the existing image waterfall.

    The feature prompt is already English, so every provider receives the exact
    same prompt.  In particular, do not run it through the generic image prompt
    translator/enricher: the final graph is composed deterministically later.
    """
    from AI import picgeneration as pg
    from AI.gigachat_image import generate_gigachat_image

    try:
        image = await generate_gigachat_image(prompt)
        if image:
            logging.info("[social_graph] image provider=gigachat")
            return image, "gigachat"

        image = await pg.pollinations_generate(prompt)
        if image:
            logging.info("[social_graph] image provider=pollinations")
            return image, "pollinations"

        image = await pg.hf_generate(prompt, "black-forest-labs/FLUX.1-schnell")
        if image:
            logging.info("[social_graph] image provider=huggingface")
            return image, "huggingface"

        image = await pg.cf_generate_t2i(prompt)
        if image:
            logging.info("[social_graph] image provider=cloudflare")
            return image, "cloudflare"
    except Exception as exc:
        logging.warning("[social_graph] image generation waterfall failed: %s", exc, exc_info=True)

    return None, None
