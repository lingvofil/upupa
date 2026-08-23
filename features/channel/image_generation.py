"""Адаптер существующего image-waterfall для автономного канала."""

from __future__ import annotations

import logging


async def generate_channel_image(prompt_ru: str) -> tuple[bytes | None, str | None]:
    """Генерирует изображение без Telegram-side-effects.

    Для автономного канала используем порядок:
    GigaChat-2 text2image -> Pollinations/Flux -> HuggingFace -> Cloudflare.

    GigaChat получает исходный русский prompt напрямую. Перевод на английский
    выполняется только если GigaChat не дал изображение и нужно перейти к
    остальным провайдерам.
    """
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
        logging.warning("[channel] image generation waterfall failed: %s", exc, exc_info=True)

    return None, None
