"""LLM composition for short context-grounded Upupa songs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import random
import re

from AI.dialog.settings import build_prompt_with_current_chat_prompt
from AI.summarize import _generate_with_active_model


SONG_MIN_LINES = 4
SONG_MAX_LINES = 8
SONG_MAX_TITLE_CHARS = 90
SONG_MAX_STYLE_CHARS = 220
SONG_LLM_ATTEMPTS = 2

_STYLE_PALETTE = (
    "raw punk",
    "Russian folk",
    "neon synth-pop",
    "sleazy chanson",
    "heavy metal",
    "cheesy disco",
    "dramatic ballad",
    "garage rock",
    "marching brass band",
    "melancholic post-punk",
    "absurd cabaret",
    "hyperactive ska",
)
_SECTION_RE = re.compile(r"^\s*\[(verse|chorus)\]\s*$", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class SongDraft:
    title: str
    style_prompt: str
    lyrics: str


class SongDraftError(RuntimeError):
    """The LLM did not produce a usable short song specification."""


def _clean_single_line(value: object, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().strip('"')
    return text[:max_chars].rstrip()


def _extract_json_object(text: str) -> dict:
    raw = _CODE_FENCE_RE.sub("", (text or "").strip())
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise SongDraftError("LLM did not return JSON")
    try:
        value = json.loads(raw[start : end + 1])
    except Exception as exc:
        raise SongDraftError("LLM returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise SongDraftError("LLM JSON is not an object")
    return value


def _sanitize_lyrics(value: object) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    raw = raw.replace("```", "")
    lines: list[str] = []
    content_lines = 0

    for raw_line in raw.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        section = _SECTION_RE.fullmatch(line)
        if section:
            normalized = f"[{section.group(1).lower()}]"
            if not lines or lines[-1] != normalized:
                lines.append(normalized)
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        if content_lines >= SONG_MAX_LINES:
            continue
        lines.append(line[:240].rstrip())
        content_lines += 1

    while lines and lines[-1].startswith("["):
        lines.pop()

    if content_lines < SONG_MIN_LINES:
        raise SongDraftError(f"too few lyric lines: {content_lines}")
    if not any(line == "[verse]" for line in lines):
        lines.insert(0, "[verse]")
    return "\n".join(lines)


def parse_song_draft(text: str) -> SongDraft:
    payload = _extract_json_object(text)
    title = _clean_single_line(payload.get("title"), SONG_MAX_TITLE_CHARS)
    style = _clean_single_line(payload.get("style") or payload.get("style_prompt"), SONG_MAX_STYLE_CHARS)
    lyrics = _sanitize_lyrics(payload.get("lyrics"))
    if not title:
        raise SongDraftError("empty title")
    if not style:
        raise SongDraftError("empty style prompt")
    return SongDraft(title=title, style_prompt=style, lyrics=lyrics)


def _build_task_prompt(*, source_context: str, subject: str, mode: str) -> str:
    palette = ", ".join(random.sample(_STYLE_PALETTE, k=7))
    if mode == "person":
        focus = (
            f"Песня строго про участника {subject}. Используй его реальные свежие реплики/поведение и "
            "релевантные ответы окружающих. Не подменяй это универсальной характеристикой человека."
        )
    else:
        focus = (
            f"Песня про недавнюю жизнь чата {subject}. Выбери 1–3 самых заметных свежих сюжета и свяжи их "
            "в одну маленькую песню; не пересказывай всю переписку подряд."
        )

    return f"""Сочини очень короткую песенную карикатуру Упупы по реальной переписке Telegram.

{focus}

Жёсткие правила:
- Факты, реплики, темы и поведение бери ТОЛЬКО из блока «Материал». Ничего не придумывай о людях и событиях.
- Нужна конкретика из этого материала: узнаваемые свежие приколы, срачи, повторяющиеся темы, странные сообщения или характерные формулировки.
- Тон: язвительный, озорной, саркастичный, с подколами и издевательствами; уместный мат разрешён. Не скатывайся в унылую мораль или абстрактные шутки.
- Lyrics: ровно {SONG_MIN_LINES}–{SONG_MAX_LINES} КОРОТКИХ строк текста песни на русском. Не пиши длинные куплеты.
- Для YuE2 используй только секции [verse] и, если реально помогает короткой песне, [chorus]. Не делай больше одного verse и одного chorus.
- Title: короткое смешное название на русском.
- Style: короткий музыкальный prompt НА АНГЛИЙСКОМ для YuE2. Подбирай жанр под содержание и варьируй его; можно смешивать стили абсурдно. Примеры направления: {palette}.
- Не объясняй результат и не используй Markdown вне текста lyrics.

Верни ТОЛЬКО валидный JSON такого вида:
{{"title":"...","style":"...","lyrics":"[verse]\\nстрока 1\\nстрока 2\\nстрока 3\\nстрока 4"}}

Материал:
{source_context}
"""


async def generate_song_draft(
    chat_id: str,
    *,
    source_context: str,
    subject: str,
    mode: str,
) -> SongDraft:
    task_prompt = _build_task_prompt(source_context=source_context, subject=subject, mode=mode)
    prompt = build_prompt_with_current_chat_prompt(
        chat_id,
        task_prompt,
        task_name="короткую песенную карикатуру по реальной переписке",
    )

    last_error: Exception | None = None
    for attempt in range(SONG_LLM_ATTEMPTS):
        current_prompt = prompt
        if attempt:
            current_prompt += (
                "\n\nПредыдущий ответ не прошёл проверку формата. Исправь: верни только JSON, "
                f"с {SONG_MIN_LINES}–{SONG_MAX_LINES} короткими строками lyrics, непустым title и English style."
            )
        raw = await _generate_with_active_model(current_prompt, chat_id)
        try:
            return parse_song_draft(raw)
        except SongDraftError as exc:
            last_error = exc
            logging.warning("[song][lyrics] invalid LLM output attempt=%s: %s", attempt + 1, exc)

    raise SongDraftError("Не удалось получить корректный короткий текст песни") from last_error
