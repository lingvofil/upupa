"""Rare bawdy verse posts for Upupa's autonomous channel."""

from __future__ import annotations

import logging
import random
import re

from features.channel import service as base
from features.channel.mood import mood_prompt
from prompts.channel import CHANNEL_PERSONA

VERSE_PROBABILITY = 0.06
VERSE_COOLDOWN_POSTS = 12
MAX_VERSE_LENGTH = 280
MAX_GENERATION_ATTEMPTS = 3

_PROFANITY_RE = re.compile(
    r"\\b(?:[её]б\\w*|бля\\w*|пизд\\w*|ху[йяеё]\\w*|нахуй|оху\\w*)\\b",
    re.IGNORECASE,
)

VERSE_FORMS = (
    (
        "частушка",
        "Ровно 4 короткие строки. Ритм разговорный, желательно с рифмой во 2-й и 4-й строках. "
        "Это хулиганская современная частушка, а не фольклорная стилизация.",
    ),
    (
        "порошок",
        "Ровно 4 короткие строки. Первые три строят нелепую сцену, четвёртая резко добивает её. "
        "Рифма не обязательна, но ритм и финальный удар обязательны.",
    ),
)


def should_try_verse(published_posts: list[dict], *, rng=random) -> bool:
    recent = published_posts[-VERSE_COOLDOWN_POSTS:]
    if any(post.get("post_kind") == "bawdy_verse" for post in recent):
        return False
    return rng.random() < VERSE_PROBABILITY


def _validate_verse(text: str, recent_posts: list[dict]) -> str | None:
    clean = (text or "").strip()
    if not clean:
        return "пустой стишок"
    if len(clean) > MAX_VERSE_LENGTH:
        return f"стишок длиннее {MAX_VERSE_LENGTH} символов"
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    if len(lines) != 4:
        return "нужно ровно четыре непустые строки"
    if not _PROFANITY_RE.search(clean):
        return "в этом формате нужен хотя бы один живой матерный акцент"
    reason = base._validate_post(clean, recent_posts)
    if reason:
        return reason
    return None


def _build_prompt(mood: dict, form: tuple[str, str], retry_note: str = "") -> str:
    name, instruction = form
    retry = (
        f"\n\nПредыдущая попытка не прошла проверку: {retry_note}. Напиши совсем другой стишок."
        if retry_note
        else ""
    )
    return f"""{CHANNEL_PERSONA}

Сейчас отдельный редкий формат канала: озорной скабрезный стишок с нецензурщиной.
Форма: {name}. {instruction}

Стишок должен быть смешным, наглым и слегка похабным. Разрешены сексуальные намёки, телесность и
нецензурная лексика; хотя бы одно матерное слово напиши без звёздочек. Не делай текст жестоким,
не используй реальных людей, usernames, конкретные чаты и любые темы с несовершеннолетними.
Не объясняй шутку и не добавляй заголовок, кавычки, нумерацию или комментарий после стиха.
Не тащи в стих недавний бытовой реквизит канала вроде чайника, холодильника, тостера, вилок,
табуреток, роутера и розеток. Лучше новая сцена, персонаж, место или нелепая ситуация.

ТВОЁ ТЕКУЩЕЕ ВНУТРЕННЕЕ СОСТОЯНИЕ:
{mood_prompt(mood)}
Пусть оно слегка влияет на наглость, но не называй его.

Ответь только четырьмя строками стишка.{retry}
"""


async def prepare_bawdy_verse(
    published_posts: list[dict],
    mood: dict,
    *,
    rng=random,
) -> tuple[str, dict] | None:
    if not should_try_verse(published_posts, rng=rng):
        return None

    from AI.summarize import _generate_with_active_model

    recent_posts = published_posts[-base.RECENT_POSTS_LIMIT:]
    form = rng.choice(VERSE_FORMS)
    retry_note = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        raw = await _generate_with_active_model(
            _build_prompt(mood, form, retry_note),
            str(base.SPECIAL_CHAT_ID),
        )
        text = (raw or "").strip()
        reason = _validate_verse(text, recent_posts)
        if not reason:
            return text, {
                "post_kind": "bawdy_verse",
                "content_mode": "bawdy_verse",
                "verse_form": form[0],
                "chat_context_used": False,
                "mood": mood.get("name"),
                "mood_posts_left": mood.get("posts_left"),
            }
        logging.warning("[channel] bawdy verse attempt %s rejected: %s", attempt, reason)
        retry_note = reason

    logging.warning("[channel] bawdy verse generation exhausted; fallback to regular post")
    return None
