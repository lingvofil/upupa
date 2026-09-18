"""Image-generation adapter for the social graph without Telegram side effects."""

from __future__ import annotations


async def generate_social_graph_image(prompt: str) -> tuple[bytes | None, str | None]:
    """Generate the anonymous portrait sheet through the shared image waterfall.

    The feature prompt is already English, so every provider receives the exact
    same prompt. In particular, do not run it through the generic image prompt
    translator/enricher: the final graph is composed deterministically later.
    """
    from features.image_generation import generate_image_bytes

    return await generate_image_bytes(
        prompt,
        translate_fallback=False,
        log_context="social_graph",
    )
