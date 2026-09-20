"""Make the DnD finale image a single readable scene instead of a comic page."""
from __future__ import annotations

import random


FINAL_IMAGE_STYLES = (
    "cinematic fantasy adventure illustration",
    "pulp adventure painting",
    "absurdist adventure painting",
    "dynamic storybook finale illustration",
    "dramatic ensemble film-still illustration",
)


def build_final_scene_prompt(campaign, session, epilogue, *, style=None) -> str:
    """Build a text-free, single-frame finale prompt from the last real events."""
    visual_style = style or random.choice(FINAL_IMAGE_STYLES)
    recent = "\n---\n".join((getattr(session, "scene_log", None) or [])[-3:])
    return (
        "Create ONE cinematic full-frame epilogue illustration for a finished absurd tabletop adventure. "
        "This must be one continuous scene at one moment in time, not a recap page.\n"
        "ABSOLUTELY NO comic panels, split screen, collage, storyboard, grids, borders, inset frames, captions, "
        "speech bubbles, thought bubbles, letters, words, UI, signs with writing, or watermarks.\n"
        f"VISUAL STYLE: {visual_style}. Favor a polished, vivid, richly colored finished illustration over a pale sketch.\n"
        f"CHARACTERS TO KEEP RECOGNIZABLE: {campaign._profile_context(session)[:1400]}\n"
        f"LAST REAL EVENTS — context only, DO NOT draw them as separate scenes:\n{recent[:1800]}\n"
        f"EPILOGUE CONSEQUENCES TO VISUALIZE: {str(epilogue or '')[:900]}\n"
        "Choose the strongest final location and stage the surviving heroes and relevant NPCs there after the adventure. "
        "Show at most two or three important consequences through concrete objects, injuries, trophies, artifacts, environment changes, "
        "or character interactions inside that SAME scene. If somebody died, do not casually place that dead hero among the survivors; "
        "only show them if the epilogue explicitly calls for a memorial, body, ghost, portrait, or other concrete depiction. "
        "Use clear silhouettes, expressive faces, cinematic lighting, depth, scene-specific props and one obvious visual focus."
    )


def install_dnd_epilogue_image(dnd) -> None:
    from AI import dnd_campaign as campaign

    if getattr(campaign, "_upupa_dnd_epilogue_image_installed", False):
        return

    def final_prompt(session, epilogue, *, style=None):
        return build_final_scene_prompt(campaign, session, epilogue, style=style)

    campaign._final_comic_prompt = final_prompt

    original_image = campaign._image

    async def image(bot, chat_id, prompt, filename, caption, *, deliver_if=None):
        if filename == "dnd_final_comic.png":
            filename = "dnd_finale.png"
            caption = "🏁 Финальный кадр. Вот до чего вы доигрались."
        return await original_image(
            bot,
            chat_id,
            prompt,
            filename,
            caption,
            deliver_if=deliver_if,
        )

    campaign._image = image
    campaign._upupa_dnd_epilogue_image_installed = True
