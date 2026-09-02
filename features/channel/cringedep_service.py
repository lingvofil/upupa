"""Rare visual-pun replies inspired by @cringedep posts."""

from __future__ import annotations

import asyncio
import logging
import random
import re

from features.channel import chat_context
from features.channel import mood_service
from features.channel import service as base
from features.channel.mood import get_current_mood, mood_prompt

CHANNEL_TARGET = mood_service.CHANNEL_TARGET
CRINGEDEP_CHANNEL = "cringedep"
CRINGEDEP_PUN_PROBABILITY = 0.05
CRINGEDEP_POSTS_LIMIT = 20

CRINGEDEP_PUN_PROMPT = """
Ты — Упупа, Telegram-бот. Иногда ты читаешь @cringedep: там в основном визуальные каламбуры,
где изображение и короткая подпись вместе образуют игру слов.

Ниже дан ОДИН реальный пост оттуда. Сначала мысленно пойми, на чём построен исходный каламбур:
какие слова, значения, созвучия и объекты изображения сцеплены между собой. Своё объяснение НЕ выводи.

После этого придумай СВОЙ новый визуальный каламбур по похожему принципу. Это не ремикс исходного поста:
не копируй исходную подпись, не заменяй в ней одну букву механически, не используй те же ключевые предметы
или персонажей, если без них можно обойтись. Нужна новая шутка из другой предметной области, но такого же
типа: короткое неожиданное слово/словосочетание, которое становится понятным при взгляде на картинку.

Картинка должна быть визуально простой и однозначной: один главный гэг, без коллажа и без длинного сюжета.
НЕ проси генератор рисовать надписи, буквы, вывески, логотипы или подпись внутри изображения — каламбур
будет отдельной подписью Telegram. Не делай инфографику или обычный мем с текстом сверху/снизу.

Подпись — сам новый каламбур. Предпочтительно 1–4 слова, максимум 8 слов и 100 символов. Разрешён мат,
если он действительно нужен шутке. Не упоминай @cringedep, источник, нейросеть, генерацию или объяснение шутки.

ИСХОДНЫЙ ПОСТ:
{source_material}

Ответь СТРОГО двумя строками и больше ничем:
КАРТИНКА: <конкретное описание новой картинки без текста внутри>
ПОДПИСЬ: <новый каламбур>
""".strip()


def _normalize_compact(text: str) -> str:
    return re.sub(r"[^\wа-яё]+", "", str(text or "").casefold(), flags=re.IGNORECASE)


def _pick_unanswered_image_post(source_posts: list[dict], published_posts: list[dict]) -> dict | None:
    """Pick a recent image post whose Telegram URL was not used before."""
    used_urls = {
        str(post.get("external_source_url"))
        for post in published_posts
        if post.get("external_source_url")
    }
    candidates = [
        post
        for post in source_posts
        if post.get("url")
        and post.get("image_url")
        and str(post.get("url")) not in used_urls
    ]
    if not candidates:
        return None
    return random.choice(candidates[-10:])


def _image_slot_available(published_posts: list[dict]) -> bool:
    """Use the same cooldown as ordinary generated channel images."""
    recent = published_posts[-base.IMAGE_POST_COOLDOWN_POSTS:]
    return not any(post.get("post_kind") == "image" for post in recent)


def _caption_repeats_source(caption: str, source_material: str) -> bool:
    compact = _normalize_compact(caption)
    if len(compact) < 4:
        return False
    return compact in _normalize_compact(source_material)


def _caption_was_recent(caption: str, published_posts: list[dict]) -> bool:
    normalized = base._normalize_for_duplicate_check(caption)
    return any(
        base._normalize_for_duplicate_check(str(post.get("external_pun_caption") or "")) == normalized
        for post in published_posts[-base.RECENT_POSTS_LIMIT:]
        if post.get("external_pun_caption")
    )


def _build_pun_prompt(source_material: str, mood: dict, retry_note: str = "") -> str:
    retry_block = (
        f"\n\nПредыдущая попытка не прошла техническую проверку: {retry_note}. "
        "Придумай совсем другой каламбур."
        if retry_note
        else ""
    )
    return (
        f"{CRINGEDEP_PUN_PROMPT.format(source_material=source_material)}\n\n"
        "ТВОЁ ТЕКУЩЕЕ ВНУТРЕННЕЕ СОСТОЯНИЕ:\n"
        f"{mood_prompt(mood)}\n"
        "Не называй состояние; пусть оно только слегка влияет на дерзость и формулировку."
        f"{retry_block}"
    )


async def _prepare_cringedep_pun(
    published_posts: list[dict],
    mood: dict,
) -> tuple[bytes, str, dict] | None:
    """Analyze one @cringedep image, invent a new pun and generate the reply image."""
    from AI.summarize import _generate_with_active_model
    from features.channel.image_generation import generate_channel_image

    source_posts = await base.fetch_public_posts(CRINGEDEP_CHANNEL, limit=CRINGEDEP_POSTS_LIMIT)
    source_post = _pick_unanswered_image_post(source_posts, published_posts)
    if source_post is None:
        return None

    try:
        image_description = await base._describe_external_image(source_post)
    except Exception as exc:
        logging.warning("[channel] @cringedep image analysis failed: %s", exc)
        return None
    if not image_description:
        logging.warning("[channel] @cringedep image could not be described, fallback to regular post")
        return None

    source_material = base._external_source_material(source_post, image_description)
    recent_posts = published_posts[-base.RECENT_POSTS_LIMIT:]
    retry_note = ""
    image_prompt = ""
    pun_caption = ""

    for attempt in range(1, base.MAX_GENERATION_ATTEMPTS + 1):
        raw = await _generate_with_active_model(
            _build_pun_prompt(source_material, mood, retry_note),
            str(base.SPECIAL_CHAT_ID),
        )
        plan = base._parse_image_plan(raw or "")
        if plan is None:
            reason = "нужны ровно две строки КАРТИНКА/ПОДПИСЬ"
        else:
            image_prompt, pun_caption = plan
            reason = base._validate_image_plan(image_prompt, pun_caption, recent_posts)
            if not reason and _caption_repeats_source(pun_caption, source_material):
                reason = "подпись повторяет исходный пост вместо нового каламбура"
            if not reason and _caption_was_recent(pun_caption, published_posts):
                reason = "такой каламбур уже недавно публиковался"

        final_caption = f"{source_post['url']}\n\n{pun_caption}" if pun_caption else ""
        if not reason and final_caption:
            reason = base._validate_post(final_caption, recent_posts)
        if not reason:
            break

        logging.warning("[channel] @cringedep pun attempt %s rejected: %s", attempt, reason)
        retry_note = reason
    else:
        logging.warning("[channel] @cringedep pun generation exhausted, fallback to regular post")
        return None

    image_bytes, provider = await generate_channel_image(image_prompt)
    if not image_bytes:
        logging.warning("[channel] @cringedep pun image providers returned no image")
        return None

    final_caption = f"{source_post['url']}\n\n{pun_caption}"
    metadata = {
        "post_kind": "image",
        "image_subtype": "external_pun_reply",
        "chat_context_used": False,
        "image_prompt": image_prompt,
        "image_provider": provider,
        "external_source_channel": f"@{CRINGEDEP_CHANNEL}",
        "external_source_url": source_post["url"],
        "external_source_has_image": True,
        "external_image_analyzed": True,
        "external_pun_caption": pun_caption,
        **mood_service._mood_metadata(mood),
    }
    return image_bytes, final_caption, metadata


async def publish_channel_post(bot, *, source: str) -> tuple[object, str]:
    """Occasionally publish a @cringedep-inspired pun, otherwise use normal mood service."""
    if random.random() >= CRINGEDEP_PUN_PROBABILITY:
        return await mood_service.publish_channel_post(bot, source=source)

    should_fallback = False
    async with base._publish_lock:
        published_posts = await asyncio.to_thread(base.load_posts)

        # Daily grounded chat context remains higher priority than the optional pun mode.
        if chat_context.should_force_chat_post(published_posts) or not _image_slot_available(published_posts):
            should_fallback = True
        else:
            mood = await asyncio.to_thread(get_current_mood)
            try:
                prepared = await _prepare_cringedep_pun(published_posts, mood)
            except Exception as exc:
                logging.warning("[channel] @cringedep pun mode failed, fallback to regular post: %s", exc, exc_info=True)
                prepared = None

            if prepared is None:
                should_fallback = True
            else:
                from aiogram import types

                image_bytes, caption, metadata = prepared
                photo = types.BufferedInputFile(image_bytes, filename="upupa-cringedep.png")
                sent = await bot.send_photo(CHANNEL_TARGET, photo, caption=caption)
                await base._store_published_post(sent, source=source, text=caption, metadata=metadata)
                await mood_service._consume_after_publish(mood, getattr(sent, "message_id", None))
                logging.info(
                    "[channel] published @cringedep pun message_id=%s source_url=%s provider=%s",
                    getattr(sent, "message_id", None),
                    metadata.get("external_source_url"),
                    metadata.get("image_provider"),
                )
                return sent, caption

    if should_fallback:
        return await mood_service.publish_channel_post(bot, source=source)

    # Defensive fallback: every branch above should already return.
    return await mood_service.publish_channel_post(bot, source=source)
