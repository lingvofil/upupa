"""Rare visual-pun replies inspired by @cringedep posts."""

from __future__ import annotations

import asyncio
import logging
import random
import re

from features.channel import chat_context, continuity
from features.channel import mood_service
from features.channel import service as base
from features.channel.mood import get_current_mood, mood_prompt

CHANNEL_TARGET = mood_service.CHANNEL_TARGET
CRINGEDEP_CHANNEL = "cringedep"
CRINGEDEP_PUN_PROBABILITY = 0.08
CRINGEDEP_POSTS_LIMIT = 20

CRINGEDEP_PUN_PROMPT = """
Ты — Упупа, Telegram-бот. У тебя особое отношение к @cringedep: ты не просто комментируешь его,
а отвечаешь собственным визуальным каламбуром, который должен быть явно рождён ИМЕННО из конкретного
исходного поста.

Ниже дан ОДИН реальный пост. Сначала мысленно разложи его механику: что изображено, какой заметный текст
есть на картинке, какие слова/имена/значения сталкиваются и почему исходный мем работает. Объяснение не выводи.

После этого придумай СВОЙ новый каламбур КАК ВЕТКУ от исходной идеи. Связь должна считываться при показе
оригинала и ответа рядом без дополнительных объяснений. Сохрани хотя бы одну центральную тематическую ось
исходника: персонажа или класс персонажей, предметную область, имя/название, музыкальную/киношную/бытовую тему
или сам тип словесной подмены. Разрешено использовать тот же ключевой объект или референс, если это помогает
связи. Не уходи в совершенно другую предметную область только ради случайного удачного слова.

Пример принципа: если исходник строится на коте + имени музыканта, ответ тоже должен оставаться в понятной
связке с котами/музыкой/именами, а не внезапно становиться каламбуром про лавровый лист. Не копируй исходную
подпись дословно и не ограничивайся заменой одной буквы без новой шутки.

Картинка должна быть визуально простой и однозначной: один главный гэг, без коллажа и длинного сюжета.
НЕ проси генератор рисовать текст: программа сама наложит каламбур на готовую картинку тем же способом,
что команда «скаламбурь».

Каламбур — предпочтительно 1–4 слова, максимум 8 слов и 100 символов. Разрешён мат, если он нужен шутке.
Не упоминай @cringedep, нейросеть, генерацию и не объясняй шутку.

ИСХОДНЫЙ ПОСТ:
{source_material}

Ответь СТРОГО двумя строками и больше ничем:
КАРТИНКА: <конкретное описание новой картинки без текста внутри>
ПОДПИСЬ: <новый каламбур, который будет наложен прямо на изображение>
""".strip()

CRINGEDEP_GROUNDING_JUDGE_PROMPT = """
Проверь только связь между исходным мемом и новым визуальным каламбуром.
Новый вариант считается связанным, только если человек, увидев оригинал и ответ рядом, поймёт,
почему ответ возник именно из этого оригинала: сохранена центральная тема, объект/класс объектов,
имя/референс или узнаваемый механизм словесной подмены.

Если это просто другой самостоятельный каламбур из иной области, ответь НЕТ.
Если связь конкретная и очевидная, ответь ДА.
Ответь ровно одним словом: ДА или НЕТ.

ИСХОДНИК:
{source_material}

НОВЫЙ ВАРИАНТ:
КАРТИНКА: {image_prompt}
ПОДПИСЬ: {pun_caption}
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


async def _is_grounded_pun(
    source_material: str,
    image_prompt: str,
    pun_caption: str,
) -> bool:
    from AI.summarize import _generate_with_active_model

    raw = await _generate_with_active_model(
        CRINGEDEP_GROUNDING_JUDGE_PROMPT.format(
            source_material=source_material,
            image_prompt=image_prompt,
            pun_caption=pun_caption,
        ),
        str(base.SPECIAL_CHAT_ID),
    )
    verdict = str(raw or "").strip().casefold().replace("ё", "е")
    return verdict.startswith("да")


async def _prepare_cringedep_pun(
    published_posts: list[dict],
    mood: dict,
) -> tuple[bytes, str, dict] | None:
    """Analyze one @cringedep image, invent a new pun and generate the reply image."""
    from AI.summarize import _generate_with_active_model
    from features.channel.image_generation import generate_channel_image, overlay_channel_text

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
            if not reason:
                try:
                    grounded = await _is_grounded_pun(source_material, image_prompt, pun_caption)
                except Exception as exc:
                    logging.warning("[channel] @cringedep grounding judge failed: %s", exc)
                    grounded = False
                if not grounded:
                    reason = "новый каламбур не связан достаточно явно с конкретным исходным постом"

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

    overlaid_image = await overlay_channel_text(image_bytes, pun_caption)
    if not overlaid_image:
        logging.warning("[channel] @cringedep pun text overlay failed")
        return None

    telegram_caption = str(source_post["url"])
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
    return overlaid_image, telegram_caption, metadata


async def _try_publish_continuity(bot, *, source: str) -> tuple[object, str] | None:
    """Give own-history continuity a chance without stealing the mandatory daily chat slot."""
    published_posts = await asyncio.to_thread(base.load_posts)
    if chat_context.should_force_chat_post(published_posts):
        return None
    if not continuity.should_try_continuity(published_posts):
        return None

    async with base._publish_lock:
        # Re-check after acquiring the shared publisher lock.
        published_posts = await asyncio.to_thread(base.load_posts)
        if chat_context.should_force_chat_post(published_posts):
            return None
        mood = await asyncio.to_thread(get_current_mood)
        try:
            prepared = await continuity.prepare_continuity_post(published_posts, mood)
        except Exception as exc:
            logging.warning("[channel] continuity mode failed, fallback to normal: %s", exc, exc_info=True)
            return None
        if prepared is None:
            return None
        text, metadata = prepared
        sent = await bot.send_message(CHANNEL_TARGET, text)
        await base._store_published_post(sent, source=source, text=text, metadata=metadata)
        await mood_service._consume_after_publish(mood, getattr(sent, "message_id", None))
        logging.info(
            "[channel] published continuity message_id=%s mode=%s arc=%s",
            getattr(sent, "message_id", None),
            metadata.get("continuity_mode"),
            metadata.get("continuity_arc"),
        )
        return sent, text


async def publish_channel_post(bot, *, source: str) -> tuple[object, str]:
    """Publish continuity/pun modes when eligible, otherwise delegate to normal mood service."""
    continuity_result = await _try_publish_continuity(bot, source=source)
    if continuity_result is not None:
        return continuity_result

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
                history_text = f"{caption}\n\n{metadata.get('external_pun_caption') or ''}".strip()
                await base._store_published_post(sent, source=source, text=history_text, metadata=metadata)
                await mood_service._consume_after_publish(mood, getattr(sent, "message_id", None))
                logging.info(
                    "[channel] published @cringedep pun message_id=%s source_url=%s provider=%s",
                    getattr(sent, "message_id", None),
                    metadata.get("external_source_url"),
                    metadata.get("image_provider"),
                )
                return sent, history_text

    if should_fallback:
        return await mood_service.publish_channel_post(bot, source=source)

    return await mood_service.publish_channel_post(bot, source=source)
