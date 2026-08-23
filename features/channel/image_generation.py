"""Адаптер существующего image-waterfall для автономного канала."""

from __future__ import annotations

import asyncio
import logging


async def generate_channel_image(prompt_ru: str) -> tuple[bytes | None, str | None]:
    """Генерирует изображение без Telegram-side-effects.

    Использует тот же порядок провайдеров, что обычная команда генерации картинок:
    Pollinations/Flux -> Kandinsky -> HuggingFace -> Cloudflare.
    """
    from AI import picgeneration as pg

    try:
        prompt_en = await pg.translate_to_en(prompt_ru)

        image = await pg.pollinations_generate(prompt_en)
        if image:
            return image, "pollinations"

        if not pg.PIPELINE_ID:
            pg.PIPELINE_ID = await asyncio.to_thread(pg.kandinsky_api.get_pipeline)
        if pg.PIPELINE_ID:
            generation_id, _ = await asyncio.to_thread(
                pg.kandinsky_api.generate,
                prompt_ru,
                pg.PIPELINE_ID,
            )
            if generation_id:
                image, _ = await asyncio.to_thread(pg.kandinsky_api.check, generation_id)
                if image:
                    return image, "kandinsky"

        image = await pg.hf_generate(prompt_en, "black-forest-labs/FLUX.1-schnell")
        if image:
            return image, "huggingface"

        image = await pg.cf_generate_t2i(prompt_en)
        if image:
            return image, "cloudflare"
    except Exception as exc:
        logging.warning("[channel] image generation waterfall failed: %s", exc, exc_info=True)

    return None, None
