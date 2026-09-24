"""Rare short voice-note posts for Upupa's autonomous channel."""

from __future__ import annotations

import logging
import random
import re

from features.channel import service as base
from features.channel.mood import mood_prompt
from prompts.channel import CHANNEL_PERSONA

VOICE_PROBABILITY = 0.03
VOICE_COOLDOWN_POSTS = 20
MAX_VOICE_TEXT_LENGTH = 220
MIN_VOICE_WORDS = 4
MAX_VOICE_WORDS = 35
MAX_GENERATION_ATTEMPTS = 3

VOICE_PROMPT = """
Сейчас редкий формат канала: вместо письменного поста ты отправишь короткое голосовое сообщение.
Напиши только то, что Упупа реально произнесёт вслух. Это не озвучка обычного поста и не мини-эссе.

Речь должна звучать разговорно: 1–3 короткие фразы, примерно 5–20 секунд. Можно материться, ворчать,
смеяться над собой, внезапно вспомнить нелепость, отреагировать на жизнь или сказать короткий стишок.
Допустимы междометия, обрыв фразы и естественная устная кривизна. Не пиши ремарки вроде «смеётся»,
не используй Markdown, списки, эмодзи, хэштеги и кавычки вокруг всего текста.

Не возвращайся к недавнему реквизиту канала только ради узнаваемости: никаких очередных чайников,
тостеров, вилок, табуреток, роутеров и розеток, если они уже были в недавней истории.
""".strip()


def should_try_voice(published_posts: list[dict], *, rng=random) -> bool:
    recent = published_posts[-VOICE_COOLDOWN_POSTS:]
    if any(post.get("post_kind") == "voice" for post in recent):
        return False
    return rng.random() < VOICE_PROBABILITY


def _validate_voice_text(text: str, recent_posts: list[dict]) -> str | None:
    clean = (text or "").strip()
    if not clean:
        return "пустой текст голосового"
    if len(clean) > MAX_VOICE_TEXT_LENGTH:
        return f"голосовое длиннее {MAX_VOICE_TEXT_LENGTH} символов"
    words = re.findall(r"\\S+", clean)
    if not MIN_VOICE_WORDS <= len(words) <= MAX_VOICE_WORDS:
        return f"нужно {MIN_VOICE_WORDS}–{MAX_VOICE_WORDS} слов"
    if "\\n-" in clean or "**" in clean or "#" in clean:
        return "текст похож на письменную разметку"
    return base._validate_post(clean, recent_posts)


def _build_prompt(
    published_posts: list[dict],
    mood: dict,
    retry_note: str = "",
) -> str:
    recent = published_posts[-10:]
    recent_block = "\\n".join(
        f"- {str(post.get('text') or '')[:220]}"
        for post in recent
        if str(post.get("text") or "").strip()
    ) or "- пока пусто"
    retry = (
        f"\\n\\nПредыдущая попытка не прошла проверку: {retry_note}. Скажи что-нибудь совсем другое."
        if retry_note
        else ""
    )
    return f"""{CHANNEL_PERSONA}

{VOICE_PROMPT}

ТВОЁ ТЕКУЩЕЕ ВНУТРЕННЕЕ СОСТОЯНИЕ:
{mood_prompt(mood)}
Не называй его.

Недавние посты — это список того, что нельзя пережёвывать снова:
{recent_block}

Ответь только текстом будущего голосового.{retry}
"""


async def prepare_voice_post(
    published_posts: list[dict],
    mood: dict,
    *,
    rng=random,
) -> tuple[bytes, str, dict] | None:
    if not should_try_voice(published_posts, rng=rng):
        return None

    from AI.summarize import _generate_with_active_model
    from services.speech import SpeechSynthesisError, synthesize_speech

    recent_posts = published_posts[-base.RECENT_POSTS_LIMIT:]
    retry_note = ""
    spoken_text = ""

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        raw = await _generate_with_active_model(
            _build_prompt(published_posts, mood, retry_note),
            str(base.SPECIAL_CHAT_ID),
        )
        spoken_text = (raw or "").strip().strip('"«»')
        reason = _validate_voice_text(spoken_text, recent_posts)
        if not reason:
            break
        logging.warning("[channel] voice text attempt %s rejected: %s", attempt, reason)
        retry_note = reason
    else:
        logging.warning("[channel] voice text generation exhausted; fallback to regular post")
        return None

    try:
        audio = await synthesize_speech(
            spoken_text,
            provider_order=("gemini",),
            allow_groq_for_cyrillic=False,
        )
    except SpeechSynthesisError as exc:
        logging.warning("[channel] voice TTS failed; fallback to regular post: %s", exc)
        return None
    except Exception as exc:
        logging.warning("[channel] voice TTS crashed; fallback to regular post: %s", exc, exc_info=True)
        return None

    return audio.data, spoken_text, {
        "post_kind": "voice",
        "content_mode": "voice",
        "chat_context_used": False,
        "voice_provider": audio.provider,
        "voice_chunks": audio.chunks,
        "mood": mood.get("name"),
        "mood_posts_left": mood.get("posts_left"),
    }
