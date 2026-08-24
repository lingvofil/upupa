"""Mood-aware orchestration for autonomous channel publications."""

from __future__ import annotations

import asyncio
import logging
import random

from features.channel import service as base
from features.channel.mood import (
    consume_mood_post,
    content_weights,
    external_probability,
    get_current_mood,
    image_probability,
    length_weights,
    mood_prompt,
)
from prompts.channel import (
    CHANNEL_IMAGE_POST_PROMPT,
    CHANNEL_PERSONA,
    EXTERNAL_COMMENT_PROMPT,
    POST_CONTENT_MODES,
    POST_LENGTH_MODES,
)

CHANNEL_TARGET = base.CHANNEL_TARGET


def _mood_metadata(mood: dict) -> dict:
    return {
        "mood": mood.get("name"),
        "mood_posts_left": mood.get("posts_left"),
    }


def _mood_block(mood: dict) -> str:
    return (
        "ТВОЁ ТЕКУЩЕЕ ВНУТРЕННЕЕ СОСТОЯНИЕ:\n"
        f"{mood_prompt(mood)}\n"
        "Не называй это состояние и не объясняй его аудитории; просто дай ему влиять на выбор мыслей и тон."
    )


def _choose_content_mode(mood: dict, *, rng=random) -> dict:
    modes = list(POST_CONTENT_MODES)
    return rng.choices(modes, weights=content_weights(modes, mood), k=1)[0]


def _choose_length_mode(mood: dict, *, rng=random) -> dict:
    modes = list(POST_LENGTH_MODES)
    return rng.choices(modes, weights=length_weights(modes, mood), k=1)[0]


def _should_try_image_post(published_posts: list[dict], mood: dict, *, rng=random) -> bool:
    recent = published_posts[-base.IMAGE_POST_COOLDOWN_POSTS:]
    if any(post.get("post_kind") == "image" for post in recent):
        return False
    return rng.random() < image_probability(mood, default=base.IMAGE_POST_PROBABILITY)


def _build_prompt(
    recent_posts: list[dict],
    chat_fragment: str | None,
    length_mode: dict,
    content_mode: dict,
    allow_batya_mention: bool,
    mood: dict,
    retry_note: str = "",
) -> str:
    prompt = base._build_prompt(
        recent_posts,
        chat_fragment,
        length_mode,
        content_mode,
        allow_batya_mention,
        retry_note,
    )
    marker = "ТИП ИМПУЛЬСА ДЛЯ ЭТОГО ПОСТА:"
    return prompt.replace(marker, f"{_mood_block(mood)}\n\n{marker}", 1)


def _external_source_material(source_post: dict, image_description: str | None) -> str:
    return base._external_source_material(source_post, image_description)


def _build_external_prompt(
    source: dict,
    source_post: dict,
    mood: dict,
    retry_note: str = "",
    *,
    image_description: str | None = None,
) -> str:
    source_material = _external_source_material(source_post, image_description)
    retry_block = (
        f"\n\nПредыдущая попытка не прошла техническую проверку: {retry_note}. Дай другой комментарий."
        if retry_note
        else ""
    )
    return (
        f"{CHANNEL_PERSONA}\n\n"
        f"{_mood_block(mood)}\n\n"
        f"{EXTERNAL_COMMENT_PROMPT.format(source_description=source['description'], source_text=source_material)}"
        f"{retry_block}"
    )


def _build_image_prompt(mood: dict, retry_note: str = "") -> str:
    retry_block = (
        f"\n\nПредыдущая попытка не прошла техническую проверку: {retry_note}. Придумай другой вариант."
        if retry_note
        else ""
    )
    return f"{CHANNEL_IMAGE_POST_PROMPT}\n\n{_mood_block(mood)}{retry_block}"


async def _try_generate_external_comment(
    published_posts: list[dict],
    recent_posts: list[dict],
    mood: dict,
) -> tuple[str, dict] | None:
    from AI.summarize import _generate_with_active_model

    sources = list(base.EXTERNAL_COMMENT_SOURCES)
    random.shuffle(sources)

    selected_source = None
    selected_post = None
    for source in sources:
        source_posts = await base.fetch_public_posts(source["channel"], limit=base.EXTERNAL_POSTS_LIMIT)
        source_post = base._pick_uncommented_external_post(source_posts, published_posts)
        if source_post:
            selected_source = source
            selected_post = source_post
            break

    if not selected_source or not selected_post:
        return None

    image_description = None
    if selected_post.get("image_url"):
        try:
            image_description = await base._describe_external_image(selected_post)
        except Exception as exc:
            logging.warning("[channel] external image mode failed: %s", exc)
        if not selected_post.get("text") and not image_description:
            logging.warning("[channel] image-only external post could not be described, fallback to normal")
            return None

    retry_note = ""
    allow_batya_reference = bool(selected_source.get("allow_batya_reference"))
    for attempt in range(1, base.MAX_GENERATION_ATTEMPTS + 1):
        prompt = _build_external_prompt(
            selected_source,
            selected_post,
            mood,
            retry_note,
            image_description=image_description,
        )
        raw_comment = await _generate_with_active_model(prompt, str(base.SPECIAL_CHAT_ID))
        comment = (raw_comment or "").strip()
        reason = base._validate_external_comment(comment, allow_batya_mention=allow_batya_reference)
        final_text = f"{selected_post['url']}\n\n{comment}"
        if not reason:
            reason = base._validate_post(final_text, recent_posts)
        if not reason:
            metadata = {
                "post_kind": "external_comment",
                "chat_context_used": False,
                "external_source_channel": f"@{selected_source['channel']}",
                "external_source_url": selected_post["url"],
                "external_source_has_image": bool(selected_post.get("image_url")),
                "external_image_analyzed": bool(image_description),
                **_mood_metadata(mood),
            }
            if selected_source.get("owner"):
                metadata["external_source_owner"] = selected_source["owner"]
            return final_text, metadata
        logging.warning(
            "[channel] external comment @%s attempt %s rejected: %s",
            selected_source["channel"],
            attempt,
            reason,
        )
        retry_note = reason

    logging.warning(
        "[channel] external comment @%s exhausted, falling back to normal post",
        selected_source["channel"],
    )
    return None


async def _try_generate_image_post(
    published_posts: list[dict],
    mood: dict,
) -> tuple[bytes, str, dict] | None:
    from AI.summarize import _generate_with_active_model
    from features.channel.image_generation import generate_channel_image

    recent_posts = published_posts[-base.RECENT_POSTS_LIMIT:]
    retry_note = ""
    image_prompt = ""
    caption = ""

    for attempt in range(1, base.MAX_GENERATION_ATTEMPTS + 1):
        raw = await _generate_with_active_model(
            _build_image_prompt(mood, retry_note),
            str(base.SPECIAL_CHAT_ID),
        )
        plan = base._parse_image_plan(raw or "")
        if plan is None:
            reason = "нужны ровно две строки КАРТИНКА/ПОДПИСЬ"
        else:
            image_prompt, caption = plan
            reason = base._validate_image_plan(image_prompt, caption, recent_posts)
        if not reason:
            break
        logging.warning("[channel] image plan attempt %s rejected: %s", attempt, reason)
        retry_note = reason
    else:
        logging.warning("[channel] image plan generation exhausted, falling back to text")
        return None

    image_bytes, provider = await generate_channel_image(image_prompt)
    if not image_bytes:
        logging.warning("[channel] image providers returned no image, falling back to text")
        return None

    return image_bytes, caption, {
        "post_kind": "image",
        "chat_context_used": False,
        "image_prompt": image_prompt,
        "image_provider": provider,
        **_mood_metadata(mood),
    }


async def generate_channel_post(mood: dict) -> tuple[str, dict]:
    from AI.summarize import _generate_with_active_model

    published_posts = await asyncio.to_thread(base.load_posts)
    recent_posts = published_posts[-base.RECENT_POSTS_LIMIT:]

    if random.random() < external_probability(mood, default=base.EXTERNAL_COMMENT_PROBABILITY):
        try:
            external_post = await _try_generate_external_comment(published_posts, recent_posts, mood)
        except Exception as exc:
            logging.warning("[channel] external comment mode failed, fallback to normal: %s", exc)
            external_post = None
        if external_post is not None:
            return external_post

    content_mode = _choose_content_mode(mood)
    chat_fragment = None
    if content_mode.get("use_chat_context"):
        chat_fragment = await asyncio.to_thread(base._pick_chat_fragment)

    length_mode = _choose_length_mode(mood)
    allow_batya_mention = base._should_allow_batya_mention(published_posts)
    retry_note = ""

    for attempt in range(1, base.MAX_GENERATION_ATTEMPTS + 1):
        prompt = _build_prompt(
            recent_posts,
            chat_fragment,
            length_mode,
            content_mode,
            allow_batya_mention,
            mood,
            retry_note,
        )
        text = await _generate_with_active_model(prompt, str(base.SPECIAL_CHAT_ID))
        reason = base._validate_post(text or "", recent_posts)
        if not reason:
            reason = base._validate_length_mode(text or "", length_mode)
        if not reason:
            reason = base._validate_batya_mention_policy(
                text or "",
                allow_batya_mention=allow_batya_mention,
            )
        if not reason:
            return text.strip(), {
                "post_kind": "normal",
                "chat_context_used": bool(chat_fragment),
                "length_mode": length_mode["name"],
                "content_mode": content_mode["name"],
                "batya_mention_allowed": allow_batya_mention,
                **_mood_metadata(mood),
            }
        logging.warning("[channel] generation attempt %s rejected: %s", attempt, reason)
        retry_note = reason

    raise RuntimeError("Не удалось получить технически валидный пост за три попытки")


async def _consume_after_publish(mood: dict, message_id: int | None) -> None:
    next_mood, changed = await asyncio.to_thread(
        consume_mood_post,
        expected_name=str(mood.get("name") or ""),
    )
    logging.info(
        "[channel] mood applied message_id=%s mood=%s posts_left_before=%s",
        message_id,
        mood.get("name"),
        mood.get("posts_left"),
    )
    if changed:
        logging.info(
            "[channel] mood changed %s -> %s posts_left=%s",
            mood.get("name"),
            next_mood.get("name"),
            next_mood.get("posts_left"),
        )


async def publish_channel_post(bot, *, source: str) -> tuple[object, str]:
    """Publish one post under a persistent mood and advance mood only after success."""
    async with base._publish_lock:
        published_posts = await asyncio.to_thread(base.load_posts)
        mood = await asyncio.to_thread(get_current_mood)

        if _should_try_image_post(published_posts, mood):
            try:
                image_post = await _try_generate_image_post(published_posts, mood)
            except Exception as exc:
                logging.warning("[channel] image mode failed, fallback to text: %s", exc, exc_info=True)
                image_post = None

            if image_post is not None:
                from aiogram import types

                image_bytes, caption, metadata = image_post
                photo = types.BufferedInputFile(image_bytes, filename="upupa-channel.png")
                sent = await bot.send_photo(CHANNEL_TARGET, photo, caption=caption)
                await base._store_published_post(sent, source=source, text=caption, metadata=metadata)
                await _consume_after_publish(mood, getattr(sent, "message_id", None))
                return sent, caption

        text, metadata = await generate_channel_post(mood)
        sent = await bot.send_message(CHANNEL_TARGET, text)
        await base._store_published_post(sent, source=source, text=text, metadata=metadata)
        await _consume_after_publish(mood, getattr(sent, "message_id", None))
        return sent, text
