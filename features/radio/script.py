"""Build a spoken Radio Upupa script from real chat messages."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass

from AI.summarize import _generate_with_active_model


RADIO_MAX_WORDS = 520
RADIO_CONTEXT_CHARS = 22000
RADIO_SUMMARY_INPUT_CHARS = 15000
RADIO_RECENT_CONTEXT_CHARS = 6500
RADIO_WORDS_PER_MINUTE = 135


@dataclass(frozen=True)
class RadioScript:
    text: str
    word_count: int
    used_structured_summary: bool

    @property
    def estimated_seconds(self) -> int:
        return round(self.word_count / RADIO_WORDS_PER_MINUTE * 60)


def _message_line(message: dict) -> str:
    name = (message.get("display_name") or message.get("username") or "Участник").strip()
    text = re.sub(r"\s+", " ", str(message.get("text") or "")).strip()
    return f"{name}: {text}"


def _join_messages(messages: list[dict], max_chars: int) -> str:
    """Take a deterministic, timeline-wide sample capped by characters."""
    lines = [_message_line(message) for message in messages if (message.get("text") or "").strip()]
    if not lines:
        return ""

    all_text = "\n".join(lines)
    if len(all_text) <= max_chars:
        return all_text

    # Preserve the whole timeline instead of taking only the newest tail.
    target_lines = max(20, min(len(lines), max_chars // 90))
    if target_lines >= len(lines):
        selected = lines
    else:
        step = (len(lines) - 1) / (target_lines - 1)
        indices = sorted({round(i * step) for i in range(target_lines)})
        selected = [lines[index] for index in indices]

    result: list[str] = []
    used = 0
    for line in selected:
        line = line[:700]
        extra = len(line) + (1 if result else 0)
        if used + extra > max_chars:
            break
        result.append(line)
        used += extra
    return "\n".join(result)


def _recent_messages(messages: list[dict], max_chars: int) -> str:
    selected: list[str] = []
    used = 0
    for message in reversed(messages):
        line = _message_line(message)[:700]
        extra = len(line) + (1 if selected else 0)
        if selected and used + extra > max_chars:
            break
        selected.append(line)
        used += extra
    return "\n".join(reversed(selected))


def _participant_stats(messages: list[dict], limit: int = 8) -> str:
    counter = Counter(
        (message.get("display_name") or message.get("username") or "Участник").strip()
        for message in messages
        if (message.get("text") or "").strip()
    )
    if not counter:
        return "нет данных"
    return ", ".join(f"{name} — {count}" for name, count in counter.most_common(limit))


def _source_size(messages: list[dict]) -> int:
    return sum(
        len(_message_line(message)) + 1
        for message in messages
        if (message.get("text") or "").strip()
    )


def sanitize_radio_script(text: str, max_words: int = RADIO_MAX_WORDS) -> str:
    """Make model output safe to speak and enforce the hard word limit."""
    result = (text or "").strip()
    result = re.sub(r"```.*?```", " ", result, flags=re.DOTALL)
    result = re.sub(r"https?://\S+|www\.\S+", "ссылка", result, flags=re.IGNORECASE)
    result = re.sub(r"(?m)^\s*[-*#>]+\s*", "", result)
    result = result.replace("**", "").replace("__", "").replace("`", "")
    result = re.sub(r"\s+", " ", result).strip()

    words = result.split()
    if len(words) <= max_words:
        return result

    limited = " ".join(words[:max_words])
    # Prefer a complete sentence near the limit. If the model produced one
    # gigantic sentence, the hard limit still wins.
    sentence_end = max(limited.rfind(". "), limited.rfind("! "), limited.rfind("? "), limited.rfind("… "))
    if sentence_end >= int(len(limited) * 0.75):
        limited = limited[: sentence_end + 1]
    return limited.strip()


async def _make_structured_summary(
    chat_id: str,
    chat_name: str,
    messages: list[dict],
    period_hours: int,
) -> str:
    sampled = _join_messages(messages, RADIO_SUMMARY_INPUT_CHARS)
    prompt = f"""Ты готовишь фактическую редакторскую выжимку для голосовой сводки Telegram-чата «{chat_name}».
Данные относятся к последним {period_hours} часам.

Извлеки только то, что действительно следует из сообщений. Ничего не додумывай.
Нужно сохранить: главные темы, конкретные заметные эпизоды, кто участвовал особенно активно, одну-две характерные или смешные детали. Если факт неясен — не утверждай его.
Пиши простым текстом без Markdown, максимум 500 слов. Это промежуточная редакторская выжимка, а не финальный выпуск.

Активность участников по числу сообщений: {_participant_stats(messages)}

Репрезентативная выборка переписки:
{sampled}
"""
    logging.info("[radio][summarize] messages=%s sampled_chars=%s", len(messages), len(sampled))
    return await _generate_with_active_model(
        prompt,
        chat_id,
        is_summarization=True,
    )


async def generate_radio_script(
    chat_id: str,
    chat_name: str | None,
    messages: list[dict],
    period_hours: int,
) -> RadioScript:
    title = chat_name or f"чат {chat_id}"
    total_context_chars = _source_size(messages)
    use_summary = total_context_chars > RADIO_CONTEXT_CHARS

    if use_summary:
        structured_summary = await _make_structured_summary(chat_id, title, messages, period_hours)
        evidence = _recent_messages(messages, RADIO_RECENT_CONTEXT_CHARS)
        source_block = (
            "Редакторская выжимка:\n"
            f"{structured_summary}\n\n"
            "Последние сообщения как дополнительная фактическая опора:\n"
            f"{evidence}"
        )
    else:
        source_block = _join_messages(messages, RADIO_CONTEXT_CHARS)

    prompt = f"""Ты — ведущий «Радио Упупы». Сделай небольшой голосовой выпуск о реальной недавней жизни Telegram-чата «{title}» за последние {period_hours} часов.

Критические правила:
- Используй только факты, темы, участников и детали из предоставленного материала. Ничего не выдумывай.
- Это разговорный радиотекст для произнесения вслух, а не письменный отчёт.
- Никакого Markdown, списков, заголовков, URL, служебных меток и сложных конструкций.
- Короткие естественные русские предложения. Ирония уместна, можно ругаться, но не превращай всё в одну повторяющуюся шутку.
- Не используй активную пользовательскую персону чата. Ведущий именно Упупа.
- Начни с короткого вступления, затем расскажи главные темы и заметные эпизоды, упомяни самых активных участников, добавь одну-две характерные или смешные детали и коротко закончи.
- Обычно цель — 330–480 русских слов. Если материала мало, делай короче и не лей воду.
- Никогда не превышай 520 слов.

Активность участников по числу сообщений: {_participant_stats(messages)}

Материал чата:
{source_block}

Верни только текст, который должен произнести ведущий.
"""

    logging.info(
        "[radio][script] messages=%s source_chars=%s prompt_context_chars=%s structured_summary=%s",
        len(messages),
        total_context_chars,
        len(source_block),
        use_summary,
    )
    raw_script = await _generate_with_active_model(prompt, chat_id, is_summarization=True)
    script = sanitize_radio_script(raw_script)
    if not script:
        raise RuntimeError("Radio script model returned empty text")

    return RadioScript(
        text=script,
        word_count=len(script.split()),
        used_structured_summary=use_summary,
    )
