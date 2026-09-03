"""Build a spoken Radio Upupa script from real chat messages."""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass

from AI.dialog.settings import build_prompt_with_current_chat_prompt
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
    valid_messages = [message for message in messages if (message.get("text") or "").strip()]
    if not valid_messages:
        return ""

    # Не создаём одновременно список всех отформатированных строк и ещё одну
    # гигантскую строку all_text. Сначала считаем размер, затем строим только
    # действительно нужный результат.
    total_chars = sum(len(_message_line(message)) for message in valid_messages)
    total_chars += max(0, len(valid_messages) - 1)
    if total_chars <= max_chars:
        return "\n".join(_message_line(message) for message in valid_messages)

    target_lines = max(20, min(len(valid_messages), max_chars // 90))
    if target_lines >= len(valid_messages):
        selected_messages = valid_messages
    else:
        step = (len(valid_messages) - 1) / (target_lines - 1)
        indices = sorted({round(i * step) for i in range(target_lines)})
        selected_messages = [valid_messages[index] for index in indices]

    result: list[str] = []
    used = 0
    for message in selected_messages:
        line = _message_line(message)[:700]
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
    *,
    world_context: str | None = None,
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

    if world_context:
        international_rule = (
            "- После основных событий чата добавь короткий блок международных новостей Мира Упупы: "
            "2–4 предложения. Используй только факты из блока «Международная обстановка». "
            "Не выдумывай причин, реакций или последствий.\n"
        )
        world_block = f"\nМеждународная обстановка:\n{world_context}\n"
    else:
        international_rule = "- Не упоминай Мир Упупы или международные новости: для этого выпуска данных нет.\n"
        world_block = ""

    task_prompt = f"""Ты — ведущий «Радио Упупы». Сделай небольшой голосовой выпуск о реальной недавней жизни Telegram-чата «{title}» за последние {period_hours} часов.

Критические правила:
- Используй только факты, темы, участников и детали из предоставленного материала. Ничего не выдумывай.
- Это разговорный радиотекст для произнесения вслух, а не письменный отчёт.
- Никакого Markdown, списков, заголовков, URL, служебных меток и сложных конструкций.
- Короткие естественные русские предложения.
- Используй характер, тон, лексику и манеру текущего промпта чата, как в команде «чобыло», но не позволяй персоне менять факты или формат радиовыпуска.
- Не используй активную пользовательскую персону как источник фактов или новых событий; она задаёт только стиль подачи. Ведущий остаётся Упупой.
- Начни с короткого вступления, затем расскажи главные темы и заметные эпизоды, упомяни самых активных участников, добавь одну-две характерные или смешные детали и коротко закончи.
{international_rule}- Обычно цель — 330–480 русских слов. Если материала мало, делай короче и не лей воду.
- Никогда не превышай 520 слов.

Активность участников по числу сообщений: {_participant_stats(messages)}

Материал чата:
{source_block}
{world_block}
Верни только текст, который должен произнести ведущий.
"""
    prompt = build_prompt_with_current_chat_prompt(
        chat_id,
        task_prompt,
        task_name="сценарий Радио Упупы",
    )

    logging.info(
        "[radio][script] messages=%s source_chars=%s prompt_context_chars=%s structured_summary=%s world_context=%s current_prompt=true",
        len(messages),
        total_context_chars,
        len(source_block),
        use_summary,
        bool(world_context),
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