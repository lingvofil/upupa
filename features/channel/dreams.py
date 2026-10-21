"""Rare dream posts: Upupa tells his own dream and mocks it like the chat dream book."""

from __future__ import annotations

from datetime import datetime
import logging
import random
import re

from AI.dream_book import DREAM_HEADER, is_dream_mention
from features.channel import service as base
from features.channel.mood import _daypart_name, mood_prompt
from features.channel.verses import _PROFANITY_RE
from prompts.channel import CHANNEL_PERSONA

DREAM_POST_PROBABILITY = 0.04
DREAM_POST_COOLDOWN_POSTS = 15
MAX_DREAM_POST_LENGTH = 900
MAX_GENERATION_ATTEMPTS = 3
RECENT_DREAMS_IN_PROMPT = 3

# Сны охотнее пересказывают спросонья и в сонном настроении.
DREAM_MOOD_MULTIPLIERS = {
    "sleepy": 2.5,
    "thoughtful": 1.3,
    "chaotic": 1.2,
    "irritated": 0.8,
}
DREAM_DAYPART_MULTIPLIERS = {
    "morning": 2.0,
    "night": 1.3,
    "day": 0.8,
    "evening": 0.6,
}

_SYMBOL_LINE_RE = re.compile(r"(?m)^[ \t]*(?:▪️?|•|-|\*)?[ \t]*\S[^\n]*?\s[—–-][ \t]+\S")
_VERDICT_LINE_RE = re.compile(r"(?mi)^[ \t]*итог[ \t]*:[ \t]*\S")


def dream_post_probability(mood: dict | None, *, now: datetime | None = None) -> float:
    name = str((mood or {}).get("name") or "")
    return (
        DREAM_POST_PROBABILITY
        * DREAM_MOOD_MULTIPLIERS.get(name, 1.0)
        * DREAM_DAYPART_MULTIPLIERS.get(_daypart_name(now), 1.0)
    )


def should_try_dream_post(
    published_posts: list[dict],
    mood: dict | None,
    *,
    rng=random,
    now: datetime | None = None,
) -> bool:
    recent = published_posts[-DREAM_POST_COOLDOWN_POSTS:]
    if any(post.get("post_kind") == "dream" for post in recent):
        return False
    return rng.random() < dream_post_probability(mood, now=now)


def _story_part(text: str) -> str:
    return text.split(DREAM_HEADER, 1)[0].strip()


def _validate_dream_post(text: str, recent_posts: list[dict]) -> str | None:
    clean = (text or "").strip()
    if not clean:
        return "пустой ответ"
    if len(clean) > MAX_DREAM_POST_LENGTH:
        return f"сон длиннее {MAX_DREAM_POST_LENGTH} символов"
    lowered = clean.casefold()
    if any(marker in lowered for marker in base._PROVIDER_ERROR_MARKERS):
        return "похоже на служебную ошибку AI-провайдера"
    if DREAM_HEADER not in clean:
        return f"нет блока «{DREAM_HEADER}» с толкованием"
    story = _story_part(clean)
    if not story:
        return "нет самого сна перед толкованием"
    if not is_dream_mention(story):
        return "рассказ должен прямо говорить, что это тебе снилось"
    if len(_SYMBOL_LINE_RE.findall(clean.split(DREAM_HEADER, 1)[1])) < 2:
        return "в толковании нужно хотя бы два образа в формате «▪️ образ — толкование»"
    if not _VERDICT_LINE_RE.search(clean):
        return "нет строки «Итог: ...»"
    if not _PROFANITY_RE.search(clean):
        return "в этом формате нужен хотя бы один живой матерный акцент"

    normalized = base._normalize_for_duplicate_check(clean)
    if any(base._normalize_for_duplicate_check(str(post.get("text") or "")) == normalized for post in recent_posts):
        return "точный дубль недавнего поста"
    repeated_motif = base._shared_household_motif(story, recent_posts)
    if repeated_motif:
        return f"бытовой мотив «{repeated_motif}» уже недавно использовался; нужен другой материал"
    previous_dreams = [post for post in recent_posts if post.get("post_kind") == "dream"]
    if base._too_similar_to_recent(story, [{"text": _story_part(str(post.get("text") or ""))} for post in previous_dreams]):
        return "сюжет слишком похож на прошлый сон; нужен совсем другой сон"
    return None


def _recent_dream_block(published_posts: list[dict]) -> str:
    dreams = [
        _story_part(str(post.get("text") or ""))
        for post in published_posts
        if post.get("post_kind") == "dream" and post.get("text")
    ][-RECENT_DREAMS_IN_PROMPT:]
    if not dreams:
        return ""
    listed = "\n".join(f"- {dream[:200]}" for dream in dreams)
    return f"\n\nПрошлые сны, которые ты уже рассказывал (не повторяй их сюжет и образы):\n{listed}"


def _build_prompt(mood: dict, published_posts: list[dict], retry_note: str = "") -> str:
    retry = (
        f"\n\nПредыдущая попытка не прошла проверку: {retry_note}. Напиши совсем другой сон."
        if retry_note
        else ""
    )
    return f"""{CHANNEL_PERSONA}

Сейчас отдельный редкий формат канала: ты рассказываешь, что тебе снилось, и тут же сам толкуешь
свой сон голосом «Сонника Упупы» — злого, циничного, матерящегося толкователя снов с дипломом
из подземного перехода. Издеваешься над собой так же, как сонник издевается над участниками чатов.

Это правило сильнее общего правила о размере постов. Структура поста строго такая:
1. Сам сон: 2–4 предложения от первого лица, начни с живой вариации «мне снилось», «приснилось»
   или «видел во сне». Сон абсурдный, конкретный и с действием: люди, места, предметы, существа,
   нелепые события. Не «странный сон», а что именно происходило.
2. Пустая строка и отдельная строка «{DREAM_HEADER}».
3. 2–4 строки «▪️ <образ из сна> — <толкование>»: псевдомистика с отсылками к выдуманным авторитетам
   (сонник Миллера, сонник бабы Зины из третьего подъезда, Фрейд после литра, Ванга по телефону)
   и издёвкой над тем, что этот образ говорит о тебе самом, твоей жизни бота, чатах и твоих привычках.
4. Строка «Итог: ...» — один издевательский диагноз себе.

Тон: сарказм, ирония, самоиздёвка, щедрый русский мат — хотя бы одно матерное слово без звёздочек,
но мат должен быть смешным, а не через каждое слово. Не используй реальных людей, usernames,
конкретные чаты и любые темы с несовершеннолетними. Без Markdown: никаких звёздочек, решёток,
жирного шрифта и заголовков, кроме строки «{DREAM_HEADER}». Весь пост — не длиннее 700 символов.
Не делай героями сна чайник, холодильник, тостер, вилки, табуретки, роутер и розетки.

ТВОЁ ТЕКУЩЕЕ ВНУТРЕННЕЕ СОСТОЯНИЕ:
{mood_prompt(mood)}
Пусть оно слегка влияет на сон и тон, но не называй его.{_recent_dream_block(published_posts)}

Ответь только самим постом.{retry}
"""


_MARKDOWN_RE = re.compile(r"(?m)\*\*|__|^[ \t]*#{1,6}[ \t]*")


def _clean(raw: str | None) -> str:
    return _MARKDOWN_RE.sub("", raw or "").strip()


async def prepare_dream_post(
    published_posts: list[dict],
    mood: dict,
    *,
    rng=random,
) -> tuple[str, dict] | None:
    if not should_try_dream_post(published_posts, mood, rng=rng):
        return None

    from AI.summarize import _generate_with_active_model

    recent_posts = published_posts[-base.RECENT_POSTS_LIMIT:]
    retry_note = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        raw = await _generate_with_active_model(
            _build_prompt(mood, published_posts, retry_note),
            str(base.SPECIAL_CHAT_ID),
        )
        text = _clean(raw)
        reason = _validate_dream_post(text, recent_posts)
        if not reason:
            return text, {
                "post_kind": "dream",
                "content_mode": "dream",
                "chat_context_used": False,
                "mood": mood.get("name"),
                "mood_posts_left": mood.get("posts_left"),
            }
        logging.warning("[channel] dream post attempt %s rejected: %s", attempt, reason)
        retry_note = reason

    logging.warning("[channel] dream post generation exhausted; fallback to other formats")
    return None
