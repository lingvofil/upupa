"""Strict child-drawing mode for the ``перерисуй`` command.

The legacy redraw prompt put the scene description first, while Pollinations
truncated prompts to 200 characters.  As a result Flux often never saw the
child-drawing style instructions and produced a normal polished illustration.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional
from urllib.parse import quote

import requests

from AI import picgeneration as pg


POLLINATIONS_PROMPT_MAX_CHARS = 1200

_CHILD_DRAWING_STYLE = (
    "BAD CHILD DRAWING. Draw as if this was made by an unskilled 4-6 year old child "
    "using cheap wax crayons and felt-tip markers on plain white paper. "
    "It must look genuinely clumsy and amateur, not like an adult artist imitating children. "
    "Use shaky crooked thick outlines, primitive shapes, wrong anatomy and proportions, "
    "asymmetric faces, awkward hands, uneven circles, flat crude colors, coloring outside the lines, "
    "scribbles, accidental overlaps, small smudges and an awkward composition. "
    "Keep the scene recognizable but badly drawn. "
    "NO professional children's-book illustration, NO polished cartoon, NO clean vector art, "
    "NO realistic shading, NO cinematic lighting, NO detailed digital painting, NO beautiful naive art. "
)


def build_childlike_redraw_prompt(description: str) -> str:
    """Put the non-negotiable style before the scene so truncation cannot hide it."""
    scene = " ".join((description or "the original scene").split())
    return f"{_CHILD_DRAWING_STYLE}SCENE TO COPY: {scene}"


def _prepare_pollinations_prompt(prompt: str) -> str:
    """Keep substantially more prompt context than the legacy 200-char cut."""
    return (prompt or "").strip()[:POLLINATIONS_PROMPT_MAX_CHARS]


async def pollinations_generate(prompt: str) -> Optional[bytes]:
    """Pollinations generator with enough prompt budget for style + scene."""
    prompt_text = _prepare_pollinations_prompt(prompt)
    prompt_q = quote(prompt_text)
    seed = random.randint(1, 99999)
    headers = {"Authorization": f"Bearer {pg.POLLINATIONS_API_KEY}"}
    queue = await pg.get_free_image_model_queue()

    def make_url(model_name: str) -> str:
        return (
            f"https://gen.pollinations.ai/image/{prompt_q}"
            f"?width=1024&height=1024&model={model_name}&seed={seed}"
        )

    for index, model_name in enumerate(queue):
        try:
            logging.info("Pollinations [%s]: %s...", model_name, prompt_text[:120])
            response = await asyncio.to_thread(
                lambda: requests.get(make_url(model_name), headers=headers, timeout=60)
            )
            logging.info(
                "Pollinations [%s] status=%s size=%s bytes",
                model_name,
                response.status_code,
                len(response.content),
            )
            if response.status_code == 200 and len(response.content) > 1000:
                return response.content
            if response.status_code == 402:
                logging.warning(
                    "Pollinations balance is empty; skipping remaining Pollinations image models"
                )
                return None
            try:
                logging.warning(
                    "Pollinations [%s] response: %s",
                    model_name,
                    response.content[:200].decode("utf-8", errors="replace"),
                )
            except Exception:
                pass
        except requests.exceptions.Timeout:
            logging.warning("Pollinations [%s] timeout — leaving provider queue", model_name)
            return None
        except Exception as exc:
            logging.error("Pollinations [%s] exception: %s", model_name, exc)

        if index < len(queue) - 1:
            await asyncio.sleep(2)

    return None


async def handle_redraw_command(message):
    """Redraw a source image as a genuinely bad drawing by a small child."""
    photo, _ = await pg.extract_image_and_prompt(message, "перерисуй")
    if not photo:
        return await message.reply("Нужно фото.")

    chat_id = str(message.chat.id)
    active_model = pg.get_active_model(chat_id)
    processing_msg = await message.reply("Анал лизирую тваю мазню")

    try:
        image_bytes = await pg.download_telegram_image(pg.bot, photo)
        analysis_prompt = (
            "Describe only the literal content needed to redraw this image. "
            "Name the main subject, action, 2-4 important objects and simple background. "
            "Use 20-40 words. No art style, lighting, camera, mood or quality adjectives."
        )
        description = await pg.analyze_image_for_redraw(
            image_bytes,
            analysis_prompt,
            active_model,
            chat_id,
        )
        final_prompt = build_childlike_redraw_prompt(description)
        logging.info("Redraw childlike prompt: %s", final_prompt[:300])

        # Prompt is already English.  Do not pass it through translate_to_en(),
        # which intentionally adds polished quality descriptors for normal image generation.
        return await pg.robust_image_generation(
            message,
            final_prompt,
            processing_msg,
            skip_translate=True,
        )
    except Exception as exc:
        logging.error("Redraw error: %s", exc, exc_info=True)
        await processing_msg.edit_text("Ошибка анализа или генерации.")
        return None


def install_into_picgeneration(picgeneration_module) -> None:
    """Install the redraw handler and the longer Pollinations prompt budget."""
    picgeneration_module.pollinations_generate = pollinations_generate
    picgeneration_module.handle_redraw_command = handle_redraw_command
