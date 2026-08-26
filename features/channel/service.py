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
from features.channel.batya_source import BATYA_CHANNEL, fetch_public_image, fetch_public_posts
from features.channel.storage import append_post, load_posts
from prompts.channel import (
    BATYA_MENTION_PROMPT,
    CHANNEL_IMAGE_POST_PROMPT,
    CHANNEL_PERSONA,
    EXTERNAL_COMMENT_PROMPT,
    POST_CONTENT_MODES,
    POST_LENGTH_MODES,
    UPUPA_CAPABILITIES,
)

CHANNEL_TARGET = os.getenv("UPUPA_CHANNEL", "@upupa_channel")
EXTERNAL_COMMENT_PROBABILITY = 0.10
IMAGE_POST_PROBABILITY = 0.15
IMAGE_POST_COOLDOWN_POSTS = 3
BATYA_MENTION_PROBABILITY = 0.04
BATYA_MENTION_COOLDOWN_POSTS = 20
RECENT_POSTS_LIMIT = 25
EXTERNAL_POSTS_LIMIT = 20
CHAT_TAIL_LIMIT = 200
CHAT_WINDOW_MIN = 12
CHAT_WINDOW_MAX = 25
MAX_POST_LENGTH = 280
MAX_EXTERNAL_COMMENT_LENGTH = 100
MAX_EXTERNAL_COMMENT_WORDS = 14
MAX_EXTERNAL_IMAGE_DESCRIPTION_CHARS = 1600
MAX_IMAGE_CAPTION_LENGTH = 100
MAX_IMAGE_CAPTION_WORDS = 8
MIN_IMAGE_PROMPT_LENGTH = 12
MAX_IMAGE_PROMPT_LENGTH = 600
MAX_GENERATION_ATTEMPTS = 3
DESIRE_OPENING_COOLDOWN_POSTS = 5
LOW_ENERGY_COOLDOWN_POSTS = 8

# Упупа знает эти публичные каналы. Описание попадает в prompt только когда код
# уже выбрал редкий режим внешнего комментария, чтобы не праймить обычные посты.
EXTERNAL_COMMENT_SOURCES = (
    {
        "channel": BATYA_CHANNEL,
        "description": f"@{BATYA_CHANNEL}; этот канал ведёт твой батя",
        "owner": None,
        "allow_batya_reference": True,
    },
    {
        "channel": "muhtarboodka",
        "description": "@muhtarboodka; этот канал ведёт Мухтар",
        "owner": "Мухтар",
        "allow_batya_reference": False,
    },
    {
        "channel": "kapibara_fen",
        "description": "@kapibara_fen",
        "owner": None,
        "allow_batya_reference": False,
    },
)

# Старые имена оставлены alias-ами для совместимости.
BATYA_COMMENT_PROBABILITY = EXTERNAL_COMMENT_PROBABILITY
BATYA_POSTS_LIMIT = EXTERNAL_POSTS_LIMIT
MAX_BATYA_COMMENT_LENGTH = MAX_EXTERNAL_COMMENT_LENGTH
MAX_BATYA_COMMENT_WORDS = MAX_EXTERNAL_COMMENT_WORDS

_publish_lock = asyncio.Lock()

_PROVIDER_ERROR_MARKERS = (
    "groq вернул пустой ответ",
    "google зассал и заблокировал ответ",
    "логов слишком много для groq",
)

_BATYA_MENTION_RE = re.compile(
    r"\b(?:бат(?:я|и|е|ю|ей)|пап(?:а|ы|е|у|ой)|от(?:ец|ца|цу|цом|це))\b",
    re.IGNORECASE,
)
_DESIRE_OPENING_RE = re.compile(r"^\s*хоч(?:у|ется)\b", re.IGNORECASE)
_LOW_ENERGY_RE = re.compile(
    r"\b(?:"
    r"застр\w*|сгни\w*|гни(?:ть|ю|ёт|ем|ете|ют)\w*|исчез\w*|"
    r"пропа(?:сть|ду|дёшь|дёт|дём|дёте|дут|дал|дала|дало|дали)\w*|"
    r"не\s+шевел\w*|ничего\s+не\s+делать|"
    r"остав(?:ь|ьте|или|ит|ят)\s+(?:меня\s+)?в\s+покое|"
    r"не\s+трог(?:ай|айте|али)\s+меня"
    r")\b",
    re.IGNORECASE,
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


def _contains_batya_mention(text: str) -> bool:
    return bool(_BATYA_MENTION_RE.search(text or ""))


def _should_allow_batya_mention(published_posts: list[dict], *, rng=random) -> bool:
    """Легенда про батю доступна редко и никогда не повторяется в соседних постах."""
    recent = published_posts[-BATYA_MENTION_COOLDOWN_POSTS:]
    if any(_contains_batya_mention(str(post.get("text") or "")) for post in recent):
        return False
    return rng.random() < BATYA_MENTION_PROBABILITY


def _should_try_image_post(published_posts: list[dict], *, rng=random) -> bool:
    """Картинки редкие и не могут идти вплотную друг к другу."""
    recent = published_posts[-IMAGE_POST_COOLDOWN_POSTS:]
    if any(post.get("post_kind") == "image" for post in recent):
        return False
    return rng.random() < IMAGE_POST_PROBABILITY


def _format_recent_posts(posts: list[dict], *, allow_batya_mention: bool) -> str:
    if not posts:
        return "(пока нет опубликованных постов)"
    chunks = []
    for index, post in enumerate(posts, 1):
        text = str(post.get("text") or "").strip()
        if not text:
            continue
        if not allow_batya_mention and _contains_batya_mention(text):
            continue
        chunks.append(f"{index}. {text}")
    return "\n\n".join(chunks) or "(недавние посты скрыты из текущего контекста)"


def _normalize_for_duplicate_check(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _starts_with_desire(text: str) -> bool:
    return bool(_DESIRE_OPENING_RE.search(text or ""))


def _contains_low_energy_motif(text: str) -> bool:
    return bool(_LOW_ENERGY_RE.search(text or ""))


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text.strip()))


def _choose_length_mode(*, rng=random) -> dict:
    modes = list(POST_LENGTH_MODES)
    return rng.choices(modes, weights=[mode["weight"] for mode in modes], k=1)[0]


def _choose_content_mode(*, rng=random) -> dict:
    modes = list(POST_CONTENT_MODES)
    return rng.choices(modes, weights=[mode["weight"] for mode in modes], k=1)[0]


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

    if _starts_with_desire(clean):
        recent_openings = recent_posts[-DESIRE_OPENING_COOLDOWN_POSTS:]
        if any(_starts_with_desire(str(post.get("text") or "")) for post in recent_openings):
            return "недавний пост уже начинался с «хочу/хочется»; нужен другой зачин"

    if _contains_low_energy_motif(clean):
        recent_tone = recent_posts[-LOW_ENERGY_COOLDOWN_POSTS:]
        if any(_contains_low_energy_motif(str(post.get("text") or "")) for post in recent_tone):
            return "недавно уже был пассивно-унылый мотив; нужен деятельный и другой импульс"

    return None


def _validate_length_mode(text: str, mode: dict) -> str | None:
    clean = text.strip()
    words = _word_count(clean)
    min_words = int(mode["min_words"])
    max_words = int(mode["max_words"])
    max_chars = int(mode["max_chars"])

    if words < min_words or words > max_words:
        return f"режим {mode['name']}: нужно {min_words}–{max_words} слов, получено {words}"
    if len(clean) > max_chars:
        return f"режим {mode['name']}: максимум {max_chars} символов, получено {len(clean)}"
    return None


def _validate_batya_mention_policy(text: str, *, allow_batya_mention: bool) -> str | None:
    if not allow_batya_mention and _contains_batya_mention(text):
        return "редкий режим бати не выбран"
    return None


def _validate_external_comment(comment: str, *, allow_batya_mention: bool = False) -> str | None:
    reason = _validate_post(comment, [])
    if reason:
        return reason
    if len(comment.strip()) > MAX_EXTERNAL_COMMENT_LENGTH:
        return f"слишком длинный комментарий ({len(comment.strip())} символов)"
    if _word_count(comment) > MAX_EXTERNAL_COMMENT_WORDS:
        return f"слишком многословный комментарий ({_word_count(comment)} слов)"
    if "https://t.me/" in comment.casefold():
        return "модель сама добавила Telegram-ссылку"
    if not allow_batya_mention and _contains_batya_mention(comment):
        return "комментарий к этому внешнему каналу не должен использовать легенду про батю"
    return None


# Совместимый alias старого имени: исторически это был комментарий именно к каналу бати.
def _validate_batya_comment(comment: str) -> str | None:
    return _validate_external_comment(comment, allow_batya_mention=True)


def _parse_image_plan(raw: str) -> tuple[str, str] | None:
    """Разбирает две строгие строки КАРТИНКА/ПОДПИСЬ от LLM."""
    lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    if len(lines) != 2:
        return None
    if not lines[0].casefold().startswith("картинка:"):
        return None
    if not lines[1].casefold().startswith("подпись:"):
        return None
    image_prompt = lines[0].split(":", 1)[1].strip()
    caption = lines[1].split(":", 1)[1].strip().strip('"«»')
    if not image_prompt or not caption:
        return None
    return image_prompt, caption


def _validate_image_plan(image_prompt: str, caption: str, recent_posts: list[dict]) -> str | None:
    prompt = image_prompt.strip()
    if len(prompt) < MIN_IMAGE_PROMPT_LENGTH:
        return "слишком короткое описание картинки"
    if len(prompt) > MAX_IMAGE_PROMPT_LENGTH:
        return "слишком длинное описание картинки"
    if "http://" in prompt.casefold() or "https://" in prompt.casefold() or "@" in prompt:
        return "в описании картинки не должно быть ссылок или usernames"
    if _contains_batya_mention(prompt):
        return "режим картинки не должен использовать легенду про батю"

    reason = _validate_post(caption, recent_posts)
    if reason:
        return reason
    if len(caption.strip()) > MAX_IMAGE_CAPTION_LENGTH:
        return f"слишком длинная подпись ({len(caption.strip())} символов)"
    if _word_count(caption) > MAX_IMAGE_CAPTION_WORDS:
        return f"слишком многословная подпись ({_word_count(caption)} слов)"
    if "http://" in caption.casefold() or "https://" in caption.casefold() or "@" in caption:
        return "в подписи не должно быть ссылок или usernames"
    if _contains_batya_mention(caption):
        return "режим картинки не должен использовать легенду про батю"
    return None


def _pick_uncommented_external_post(source_posts: list[dict], published_posts: list[dict]) -> dict | None:
    """Не комментируем одну и ту же внешнюю ссылку повторно."""
    used_urls = {
        str(post.get("external_source_url"))
        for post in published_posts
        if post.get("external_source_url")
    }
    candidates = [
        post
        for post in source_posts
        if post.get("url") not in used_urls and (post.get("text") or post.get("image_url"))
    ]
    if not candidates:
        return None
    return random.choice(candidates[-10:])


# Совместимый alias старого имени.
def _pick_uncommented_batya_post(source_posts: list[dict], published_posts: list[dict]) -> dict | None:
    return _pick_uncommented_external_post(source_posts, published_posts)


def _build_prompt(
    recent_posts: list[dict],
    chat_fragment: str | None,
    length_mode: dict,
    content_mode: dict,
    allow_batya_mention: bool,
    retry_note: str = "",
) -> str:
    context_block = (
        "\n\nСЛУЧАЙНЫЙ ФРАГМЕНТ ОДНОГО ИЗ ЧАТОВ (необязательный материал):\n"
        f"{chat_fragment}"
        if chat_fragment
        else ""
    )
    capabilities_block = (
        f"\n\n{UPUPA_CAPABILITIES}"
        if content_mode.get("include_capabilities")
        else ""
    )
    batya_block = f"\n\n{BATYA_MENTION_PROMPT}" if allow_batya_mention else ""
    retry_block = (
        f"\n\nПредыдущая попытка не прошла техническую проверку: {retry_note}. Напиши другой вариант."
        if retry_note
        else ""
    )
    return (
        f"{CHANNEL_PERSONA}"
        f"{capabilities_block}"
        f"{batya_block}\n\n"
        f"ТИП ИМПУЛЬСА ДЛЯ ЭТОГО ПОСТА: {content_mode['instruction']}\n\n"
        f"РАЗМЕР ЭТОГО ПОСТА — ОБЯЗАТЕЛЬНО: {length_mode['instruction']}\n\n"
        "ТВОИ НЕДАВНИЕ ПОСТЫ:\n"
        f"{_format_recent_posts(recent_posts, allow_batya_mention=allow_batya_mention)}"
        f"{context_block}"
        f"{retry_block}\n\n"
        "ТЕКУЩИЙ ПОСТ:"
    )


def _external_source_material(source_post: dict, image_description: str | None) -> str:
    parts: list[str] = []
    source_text = str(source_post.get("text") or "").strip()
    if source_text:
        parts.append(f"ТЕКСТ:\n{source_text[:5000]}")
    if image_description:
        parts.append(f"ОПИСАНИЕ ФОТО:\n{image_description[:MAX_EXTERNAL_IMAGE_DESCRIPTION_CHARS]}")
    return "\n\n".join(parts) or "(нет доступного текстового содержимого)"


def _build_external_prompt(
    source: dict,
    source_post: dict,
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
        f"{EXTERNAL_COMMENT_PROMPT.format(source_description=source['description'], source_text=source_material)}"
        f"{retry_block}"
    )


def _build_image_prompt(retry_note: str = "") -> str:
    retry_block = (
        f"\n\nПредыдущая попытка не прошла техническую проверку: {retry_note}. Придумай другой вариант."
        if retry_note
        else ""
    )
    return f"{CHANNEL_IMAGE_POST_PROMPT}{retry_block}"


async def _describe_external_image(source_post: dict) -> str | None:
    """Лениво распознаёт фото только у уже выбранного внешнего поста."""
    image_url = str(source_post.get("image_url") or "").strip()
    if not image_url:
        return None

    downloaded = await fetch_public_image(image_url)
    if not downloaded:
        return None
    image_bytes, mime_type = downloaded

    try:
        from AI.whatisthere import analyze_media_bytes

        description = await analyze_media_bytes(
            image_bytes,
            mime_type,
            custom_prompt=(
                "Кратко и нейтрально опиши изображение для другой модели, которая будет писать комментарий. "
                "Укажи основные объекты, людей, действие и заметный текст на картинке. Ничего не выдумывай"
            ),
            chat_id=SPECIAL_CHAT_ID,
        )
    except Exception as exc:
        logging.warning("[channel] external image vision failed: %s", exc)
        return None

    clean = str(description or "").strip()
    lowered = clean.casefold()
    if not clean or lowered.startswith("нихуя не понял"):
        return None
    if any(marker in lowered for marker in _PROVIDER_ERROR_MARKERS):
        return None
    return clean[:MAX_EXTERNAL_IMAGE_DESCRIPTION_CHARS]


async def _try_generate_external_comment(
    published_posts: list[dict],
    recent_posts: list[dict],
) -> tuple[str, dict] | None:
    """Редкий режим: ссылка на свежий пост одного знакомого канала + реакция Упупы."""
    from AI.summarize import _generate_with_active_model

    sources = list(EXTERNAL_COMMENT_SOURCES)
    random.shuffle(sources)

    selected_source = None
    selected_post = None
    for source in sources:
        source_posts = await fetch_public_posts(source["channel"], limit=EXTERNAL_POSTS_LIMIT)
        source_post = _pick_uncommented_external_post(source_posts, published_posts)
        if source_post:
            selected_source = source
            selected_post = source_post
            break

    if not selected_source or not selected_post:
        return None

    image_description = None
    if selected_post.get("image_url"):
        try:
            image_description = await _describe_external_image(selected_post)
        except Exception as exc:
            logging.warning("[channel] external image mode failed: %s", exc)
        if not selected_post.get("text") and not image_description:
            logging.warning("[channel] image-only external post could not be described, fallback to normal")
            return None

    retry_note = ""
    allow_batya_reference = bool(selected_source.get("allow_batya_reference"))
    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        prompt = _build_external_prompt(
            selected_source,
            selected_post,
            retry_note,
            image_description=image_description,
        )
        raw_comment = await _generate_with_active_model(prompt, str(SPECIAL_CHAT_ID))
        comment = (raw_comment or "").strip()
        reason = _validate_external_comment(comment, allow_batya_mention=allow_batya_reference)
        final_text = f"{selected_post['url']}\n\n{comment}"
        if not reason:
            reason = _validate_post(final_text, recent_posts)
        if not reason:
            metadata = {
                "post_kind": "external_comment",
                "chat_context_used": False,
                "external_source_channel": f"@{selected_source['channel']}",
                "external_source_url": selected_post["url"],
                "external_source_has_image": bool(selected_post.get("image_url")),
                "external_image_analyzed": bool(image_description),
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


async def _try_generate_batya_comment(
    published_posts: list[dict],
    recent_posts: list[dict],
) -> tuple[str, dict] | None:
    """Совместимый wrapper старого имени: теперь выбирает любой внешний источник."""
    return await _try_generate_external_comment(published_posts, recent_posts)


async def _try_generate_image_post(published_posts: list[dict]) -> tuple[bytes, str, dict] | None:
    """Придумывает изображение и подпись, затем запускает существующий image-waterfall."""
    from AI.summarize import _generate_with_active_model
    from features.channel.image_generation import generate_channel_image

    recent_posts = published_posts[-RECENT_POSTS_LIMIT:]
    retry_note = ""
    image_prompt = ""
    caption = ""

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        raw = await _generate_with_active_model(_build_image_prompt(retry_note), str(SPECIAL_CHAT_ID))
        plan = _parse_image_plan(raw or "")
        if plan is None:
            reason = "нужны ровно две строки КАРТИНКА/ПОДПИСЬ"
        else:
            image_prompt, caption = plan
            reason = _validate_image_plan(image_prompt, caption, recent_posts)
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
    }


async def generate_channel_post() -> tuple[str, dict]:
    """Генерирует один текстовый пост и метаданные о его происхождении."""
    from AI.summarize import _generate_with_active_model

    published_posts = await asyncio.to_thread(load_posts)
    recent_posts = published_posts[-RECENT_POSTS_LIMIT:]

    if random.random() < EXTERNAL_COMMENT_PROBABILITY:
        try:
            external_post = await _try_generate_external_comment(published_posts, recent_posts)
        except Exception as exc:
            logging.warning("[channel] external comment mode failed, fallback to normal: %s", exc)
            external_post = None
        if external_post is not None:
            return external_post

    content_mode = _choose_content_mode()
    chat_fragment = None
    if content_mode.get("use_chat_context"):
        chat_fragment = await asyncio.to_thread(_pick_chat_fragment)

    length_mode = _choose_length_mode()
    allow_batya_mention = _should_allow_batya_mention(published_posts)
    retry_note = ""

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        prompt = _build_prompt(
            recent_posts,
            chat_fragment,
            length_mode,
            content_mode,
            allow_batya_mention,
            retry_note,
        )
        text = await _generate_with_active_model(prompt, str(SPECIAL_CHAT_ID))
        reason = _validate_post(text or "", recent_posts)
        if not reason:
            reason = _validate_length_mode(text or "", length_mode)
        if not reason:
            reason = _validate_batya_mention_policy(
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
            }
        logging.warning("[channel] generation attempt %s rejected: %s", attempt, reason)
        retry_note = reason

    raise RuntimeError("Не удалось получить технически валидный пост за три попытки")


async def _store_published_post(sent, *, source: str, text: str, metadata: dict) -> dict:
    record = {
        "created_at": datetime.now().isoformat(),
        "source": source,
        "text": text,
        "message_id": getattr(sent, "message_id", None),
        **metadata,
    }
    await asyncio.to_thread(append_post, record)
    logging.info(
        "[channel] published source=%s message_id=%s kind=%s length=%s content=%s batya=%s context=%s external=%s image_provider=%s external_image=%s",
        source,
        record["message_id"],
        record.get("post_kind"),
        record.get("length_mode"),
        record.get("content_mode"),
        record.get("batya_mention_allowed"),
        record.get("chat_context_used"),
        record.get("external_source_channel"),
        record.get("image_provider"),
        record.get("external_image_analyzed"),
    )
    return record


async def publish_channel_post(bot, *, source: str) -> tuple[object, str]:
    """Публикует текст или редкую картинку; историю пишет только после успешной отправки."""
    async with _publish_lock:
        published_posts = await asyncio.to_thread(load_posts)

        if _should_try_image_post(published_posts):
            try:
                image_post = await _try_generate_image_post(published_posts)
            except Exception as exc:
                logging.warning("[channel] image mode failed, fallback to text: %s", exc, exc_info=True)
                image_post = None

            if image_post is not None:
                from aiogram import types

                image_bytes, caption, metadata = image_post
                photo = types.BufferedInputFile(image_bytes, filename="upupa-channel.png")
                sent = await bot.send_photo(CHANNEL_TARGET, photo, caption=caption)
                await _store_published_post(sent, source=source, text=caption, metadata=metadata)
                return sent, caption

        text, metadata = await generate_channel_post()
        sent = await bot.send_message(CHANNEL_TARGET, text)
        await _store_published_post(sent, source=source, text=text, metadata=metadata)
        return sent, text
