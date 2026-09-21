"""Shared image-generation waterfall without Telegram side effects."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import nullcontext

from infrastructure.ai.execution import ai_execution_lane


async def generate_image_bytes(
    prompt: str,
    *,
    translate_fallback: bool = True,
    log_context: str = "image",
    should_continue: Callable[[], bool] | None = None,
) -> tuple[bytes | None, str | None]:
    """Generate image bytes through the project standard image waterfall.

    Normal image flows preserve their existing provider order. DnD uses a
    latency-aware order: GigaChat first, then quick reserves
    Pollinations/Kandinsky/Hugging Face, with slow public AI Horde last.
    DnD provider work runs in the background AI lane and can stop between
    providers when the scene that requested the image is no longer current.
    """
    from AI import picgeneration as pg
    from AI.aihorde_image import generate_aihorde_image
    from AI.gigachat_image import generate_gigachat_image

    is_dnd = log_context == "dnd"
    if is_dnd:
        translate_fallback = False

    def active() -> bool:
        if should_continue is None:
            return True
        try:
            return bool(should_continue())
        except Exception:
            logging.exception("[%s] image continuation guard failed", log_context)
            return False

    def stale(stage: str) -> bool:
        if active():
            return False
        logging.info("[%s] image waterfall stopped as stale before %s", log_context, stage)
        return True

    lane = ai_execution_lane("background") if is_dnd else nullcontext()

    try:
        with lane:
            if stale("gigachat"):
                return None, None
            image = await generate_gigachat_image(prompt)
            if image:
                logging.info("[%s] image provider=gigachat", log_context)
                return image, "gigachat"

            if stale("fallback prompt"):
                return None, None
            fallback_prompt = await pg.translate_to_en(prompt) if translate_fallback else prompt

            if is_dnd:
                if stale("pollinations"):
                    return None, None
                image = await pg.pollinations_generate(fallback_prompt)
                if image:
                    logging.info("[%s] image provider=pollinations", log_context)
                    return image, "pollinations"

                if stale("kandinsky"):
                    return None, None
                image = await pg.kandinsky_generate(fallback_prompt)
                if image:
                    logging.info("[%s] image provider=kandinsky", log_context)
                    return image, "kandinsky"

                if stale("huggingface"):
                    return None, None
                image = await pg.hf_generate(fallback_prompt, "black-forest-labs/FLUX.1-schnell")
                if image:
                    logging.info("[%s] image provider=huggingface", log_context)
                    return image, "huggingface"

                if stale("aihorde"):
                    return None, None
                image = await generate_aihorde_image(
                    fallback_prompt,
                    should_continue=should_continue,
                )
                if image:
                    logging.info("[%s] image provider=aihorde", log_context)
                    return image, "aihorde"
                return None, None

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
        logging.warning(
            "[%s] image generation waterfall failed: %s",
            log_context,
            exc,
            exc_info=True,
        )

    return None, None
