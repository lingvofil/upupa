"""Генерация и публикация постов автономного канала Упупы."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from datetime import datetime

from core.settings import SPECIAL_CHAT_ID
from core.state import chat_list
from features.channel.batya_source import BATYA_CHANNEL, fetch_batya_posts
from features.channel.storage import append_post, load_posts
from prompts.channel import (
    BATYA_COMMENT_PROMPT,
    CHANNEL_PERSONA,
    OPTIONAL_NUDGES,
    UPUPA_CAPABILITIES,
)

CHANNEL_TARGET = os.getenv("UPUPA_CHANNEL", "@upupa_channel")
CHAT_CONTEXT_PROBABILITY = 0.25
BATYA_COMMENT_PROBABILITY = 0.10
RECENT_POSTS_LIMIT = 25
BATYA_POSTS_LIMIT = 20
CHAT_TAIL_LIMIT = 200
CHAT_WINDOW_MIN = 12
CHAT_WINDOW_MAX = 25
MAX_POST_LENGTH = 1200
MAX_BATYA_COMMENT_LENGTH = 400
MAX_GENERATION_ATTEMPTS = 3

_publish_lock = asyncio.Lock()

_PROVIDER_ERROR_MARKERS = (
    "groq вернул пустой ответ",
    "google зассал и заблокировал ответ",
    "логов слишком много для groq",
)


def _sanitize_chat_text(text: str) -> str:
    text = re.sub(r"@[A-Za-z0-9_]{3,}", "@пользователь", text)
    text = re.sub(r"https?://\S+", "[ссылка]", text)
    text = re.sub(r"\b\d{7,}\b", "[номер]", text)
    return text.strip()


def _anonymize_fragment(messages: list[dict]) -> str:
    aliases: dict[str, str] = {}
    lines: list[str] = []
    for message in messages:
        raw_name = str(message.get("name") or "участник")
        if raw_name not in aliases:
            aliases[raw_name] = f"Участник {len(aliases) + 1}"
        text = _sanitize_chat_text(str(message.get("text") or ""))
        if text:
            lines.append(f"{aliases[raw_name]}: {text}")
    return "\n".join(lines)


def _pick_chat_fragment() -> str | None:
    """Берёт случайный относительно свежий фрагмент одного группового чата."""
    from AI.chat_recall import _read_chat_log

    candidates = []
    for chat in chat_list:
        try:
            chat_id = int(chat.get("id"))
        except (TypeError, ValueError):
            continue
        if chat_id < 0:
            candidates.append(chat_id)

    random.shuffle(candidates)
    for chat_id in candidates[:10]:
        messages = _read_chat_log(str(chat_id))
        if len(messages) < CHAT_WINDOW_MIN:
            continue
        tail = messages[-CHAT_TAIL_LIMIT:]
        window_size = min(len(tail), random.randint(CHAT_WINDOW_MIN, CHAT_WINDOW_MAX))
        start_max = len(tail) - window_size
        start = random.randint(0, start_max) if start_max > 0 else 0
        fragment = _anonymize_fragment(tail[start:start + window_size])
        if fragment:
            return fragment
    return None


def _format_recent_posts(posts: list[dict]) -> str:
    if not posts:
        return "(пока нет опубликованных постов)"
    chunks = []
    for index, post in enumerate(posts, 1):
        text = str(post.get("text") or "").strip()
        if text:
            chunks.append(f"{index}. {text}")
    return "\n\n".join(chunks) or "(пока нет опубликованных постов)"


def _normalize_for_duplicate_check(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _validate_post(text: str, recent_posts: list[dict]) -> str | None:
    clean = text.strip()
    if not clean:
        return "пустой ответ"
    if len(clean) > MAX_POST_LENGTH:
        return f"слишком длинный пост ({len(clean)} символов)"
    lowered = clean.casefold()
    if any(marker in lowered for marker in _PROVIDER_ERROR_MARKERS):
        return "похоже на служебную ошибку AI-провайдера"

    normalized = _normalize_for_duplicate_check(clean)
    recent_normalized = {
        _normalize_for_duplicate_check(str(post.get("text") or ""))
        for post in recent_posts
        if post.get("text")
    }
    if normalized in recent_normalized:
        return "точный дубль недавнего поста"
    return None


def _validate_batya_comment(comment: str) -> str | None:
    reason = _validate_post(comment, [])
    if reason:
        return reason
    if len(comment.strip()) > MAX_BATYA_COMMENT_LENGTH:
        return f"слишком длинный комментарий ({len(comment.strip())} символов)"
    if "https://t.me/" in comment.casefold():
        return "модель сама добавила Telegram-ссылку"
    return None


def _pick_uncommented_batya_post(source_posts: list[dict], published_posts: list[dict]) -> dict | None:
    """Не комментируем одну и ту же ссылку повторно."""
    used_urls = {
        str(post.get("external_source_url"))
        for post in published_posts
        if post.get("external_source_url")
    }
    candidates = [post for post in source_posts if post.get("url") not in used_urls and post.get("text")]
    if not candidates:
        return None
    # Берём случайный из свежих, чтобы канал не выглядел как строгий RSS-репостер.
    return random.choice(candidates[-10:])


def _build_prompt(recent_posts: list[dict], chat_fragment: str | None, retry_note: str = "") -> str:
    nudge = random.choice(OPTIONAL_NUDGES)
    context_block = (
        "\n\nСЛУЧАЙНЫЙ ФРАГМЕНТ ОДНОГО ИЗ ЧАТОВ (необязательный материал):\n"
        f"{chat_fragment}"
        if chat_fragment
        else "\n\nФрагмент чужой переписки в этот раз не дан."
    )
    retry_block = f"\n\nПредыдущая попытка не прошла техническую проверку: {retry_note}. Напиши другой вариант." if retry_note else ""
    return (
        f"{CHANNEL_PERSONA}\n\n"
        f"{UPUPA_CAPABILITIES}\n\n"
        f"Необязательный импульс для этого раза: {nudge}\n\n"
        "ТВОИ НЕДАВНИЕ ПОСТЫ:\n"
        f"{_format_recent_posts(recent_posts)}"
        f"{context_block}"
        f"{retry_block}\n\n"
        "ТЕКУЩИЙ ПОСТ:"
    )


def _build_batya_prompt(source_post: dict, retry_note: str = "") -> str:
    source_text = str(source_post.get("text") or "")[:5000]
    retry_block = f"\n\nПредыдущая попытка не прошла техническую проверку: {retry_note}. Дай другой комментарий." if retry_note else ""
    return (
        f"{CHANNEL_PERSONA}\n\n"
        f"{BATYA_COMMENT_PROMPT.format(batya_channel=f'@{BATYA_CHANNEL}', source_text=source_text)}"
        f"{retry_block}"
    )


async def _try_generate_batya_comment(
    published_posts: list[dict],
    recent_posts: list[dict],
) -> tuple[str, dict] | None:
    """Редкий режим: ссылка на реальный пост бати + короткий комментарий Упупы."""
    from AI.summarize import _generate_with_active_model

    source_posts = await fetch_batya_posts(limit=BATYA_POSTS_LIMIT)
    source_post = _pick_uncommented_batya_post(source_posts, published_posts)
    if not source_post:
        return None

    retry_note = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        prompt = _build_batya_prompt(source_post, retry_note)
        raw_comment = await _generate_with_active_model(prompt, str(SPECIAL_CHAT_ID))
        comment = (raw_comment or "").strip()
        reason = _validate_batya_comment(comment)
        final_text = f"{source_post['url']}\n\n{comment}"
        if not reason:
            reason = _validate_post(final_text, recent_posts)
        if not reason:
            return final_text, {
                "post_kind": "batya_comment",
                "chat_context_used": False,
                "external_source_channel": f"@{BATYA_CHANNEL}",
                "external_source_url": source_post["url"],
            }
        logging.warning("[channel] batya comment attempt %s rejected: %s", attempt, reason)
        retry_note = reason

    logging.warning("[channel] batya comment generation exhausted, falling back to normal post")
    return None


async def generate_channel_post() -> tuple[str, dict]:
    """Генерирует один пост и метаданные о его происхождении."""
    from AI.summarize import _generate_with_active_model

    published_posts = await asyncio.to_thread(load_posts)
    recent_posts = published_posts[-RECENT_POSTS_LIMIT:]

    if random.random() < BATYA_COMMENT_PROBABILITY:
        try:
            batya_post = await _try_generate_batya_comment(published_posts, recent_posts)
        except Exception as exc:
            logging.warning("[channel] batya comment mode failed, fallback to normal: %s", exc)
            batya_post = None
        if batya_post is not None:
            return batya_post

    chat_fragment = None
    if random.random() < CHAT_CONTEXT_PROBABILITY:
        chat_fragment = await asyncio.to_thread(_pick_chat_fragment)

    retry_note = ""
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        prompt = _build_prompt(recent_posts, chat_fragment, retry_note)
        text = await _generate_with_active_model(prompt, str(SPECIAL_CHAT_ID))
        reason = _validate_post(text or "", recent_posts)
        if not reason:
            return text.strip(), {
                "post_kind": "normal",
                "chat_context_used": bool(chat_fragment),
            }
        logging.warning("[channel] generation attempt %s rejected: %s", attempt, reason)
        retry_note = reason

    raise RuntimeError("Не удалось получить технически валидный пост за три попытки")


async def publish_channel_post(bot, *, source: str) -> tuple[object, str]:
    """Генерирует пост, публикует в канал и только после успеха сохраняет в историю."""
    async with _publish_lock:
        text, metadata = await generate_channel_post()
        sent = await bot.send_message(CHANNEL_TARGET, text)
        record = {
            "created_at": datetime.now().isoformat(),
            "source": source,
            "text": text,
            "message_id": getattr(sent, "message_id", None),
            **metadata,
        }
        await asyncio.to_thread(append_post, record)
        logging.info(
            "[channel] published source=%s message_id=%s kind=%s context=%s",
            source,
            record["message_id"],
            record.get("post_kind"),
            record.get("chat_context_used"),
        )
        return sent, text
