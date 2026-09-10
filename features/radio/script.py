"""Build a spoken Radio Upupa script from real chat messages."""

from __future__ import annotations

import logging
import random
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
RADIO_DURATION_MINUTES = (1, 3, 5)
RADIO_DEFAULT_DURATION_MINUTES = 3
RADIO_DURATION_WORD_RANGES = {
    1: (100, 140, 160),
    3: (330, 480, RADIO_MAX_WORDS),
    5: (580, 720, 760),
}

RADIO_RUBRICS = (
    ("главные новости", "коротко собери 1–2 главных события или темы выпуска"),
    ("человек выпуска", "выдели одного реально заметного участника и объясни по материалу, чем он отметился"),
    ("спорная территория", "если в материале есть спор или столкновение мнений, коротко разложи его; иначе пропусти"),
    ("что это вообще было", "выбери один особенно странный, смешной или характерный эпизод и коротко прокомментируй"),
    ("культурная страница", "заметь мем, шутку, бытовую тему, фильм, музыку, еду или другой культурный след, если он реально есть"),
    ("прогноз Упупы", "сделай шуточный прогноз только как интерпретацию уже видимого паттерна; не выдавай его за факт"),
)

SPEAKER_HOST = "ВЕДУЩИЙ"
SPEAKER_EXPERT = "ЭКСПЕРТ"


@dataclass(frozen=True)
class RadioScript:
    text: str
    word_count: int
    used_structured_summary: bool

    @property
    def estimated_seconds(self) -> int:
        return round(self.word_count / RADIO_WORDS_PER_MINUTE * 60)


def get_radio_word_targets(duration_minutes: int) -> tuple[int, int, int]:
    try:
        return RADIO_DURATION_WORD_RANGES[duration_minutes]
    except KeyError as exc:
        raise ValueError(f"Unsupported radio duration: {duration_minutes}") from exc


def _message_line(message: dict) -> str:
    name = (message.get("display_name") or message.get("username") or "Участник").strip()
    text = re.sub(r"\s+", " ", str(message.get("text") or "")).strip()
    return f"{name}: {text}"


def _join_messages(messages: list[dict], max_chars: int) -> str:
    """Take a deterministic, timeline-wide sample capped by characters."""
    valid_messages = [message for message in messages if (message.get("text") or "").strip()]
    if not valid_messages:
        return ""

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


def _participant_names(messages: list[dict], limit: int = 8) -> str:
    counter = Counter(
        (message.get("display_name") or message.get("username") or "Участник").strip()
        for message in messages
        if (message.get("text") or "").strip()
    )
    if not counter:
        return "нет данных"
    return ", ".join(name for name, _count in counter.most_common(limit))


def _source_size(messages: list[dict]) -> int:
    return sum(
        len(_message_line(message)) + 1
        for message in messages
        if (message.get("text") or "").strip()
    )


def _choose_rubrics(
    *,
    world_context: str | None,
    social_context: str | None = None,
    rng=random,
) -> tuple[tuple[str, str], ...]:
    """Pick varied editorial rubrics; data-gated rubrics are always included."""
    choices = list(RADIO_RUBRICS)
    count = min(len(choices), rng.randint(2, 4))
    picked = rng.sample(choices, k=count)
    if social_context:
        picked.append((
            "кто с кем",
            "естественно отметь хотя бы один реальный паттерн общения из социальных наблюдений, без технических метрик",
        ))
    if world_context:
        picked.append((
            "международная панорама",
            "коротко упомяни 1–2 факта Мира Упупы только из блока международной обстановки",
        ))
    return tuple(picked)


def _rubrics_prompt(rubrics: tuple[tuple[str, str], ...]) -> str:
    return "\n".join(f"- «{name}»: {instruction}." for name, instruction in rubrics)


def sanitize_radio_script(text: str, max_words: int = RADIO_MAX_WORDS) -> str:
    """Make model output safe to speak and enforce the hard word limit."""
    result = (text or "").strip()
    result = re.sub(r"```.*?```", " ", result, flags=re.DOTALL)
    result = re.sub(r"https?://\S+|www\.\S+", "ссылка", result, flags=re.IGNORECASE)
    result = re.sub(r"(?m)^\s*[-*#>]+\s*", "", result)
    result = result.replace("**", "").replace("__", "").replace("`", "")
    # Keep speaker labels because dual-voice synthesis parses them later.
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

Заметные по активности участники: {_participant_names(messages)}

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
    social_context: str | None = None,
    duration_minutes: int = RADIO_DEFAULT_DURATION_MINUTES,
) -> RadioScript:
    target_min_words, target_max_words, hard_max_words = get_radio_word_targets(duration_minutes)
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

    if social_context:
        social_rule = (
            "- В блоке «Социальные наблюдения» есть фактические паттерны реплаев, упоминаний и реакций. "
            "Обязательно вплети хотя бы один из них в выпуск естественным языком. Не называй источник соцграфом и не произноси технические метрики.\n"
        )
        social_block = f"\nСоциальные наблюдения:\n{social_context}\n"
    else:
        social_rule = ""
        social_block = ""

    if world_context:
        international_rule = (
            "- Международные факты бери только из блока «Международная обстановка». "
            "Не выдумывай причин, реакций или последствий.\n"
        )
        world_block = f"\nМеждународная обстановка:\n{world_context}\n"
    else:
        international_rule = "- Не упоминай Мир Упупы или международные новости: для этого выпуска данных нет.\n"
        world_block = ""

    rubrics = _choose_rubrics(world_context=world_context, social_context=social_context)
    task_prompt = f"""Ты — ведущий «Радио Упупы». Сделай голосовой выпуск о реальной недавней жизни Telegram-чата «{title}» за последние {period_hours} часов.

Критические правила:
- Используй только факты, темы, участников и детали из предоставленного материала. Ничего не выдумывай.
- Это разговорный радиотекст для произнесения вслух, а не письменный отчёт.
- Никакого Markdown, URL и сложных конструкций. Короткие естественные русские предложения.
- Не сообщай количество сообщений, размер выборки или технические счётчики.
- Используй характер, тон, лексику и манеру текущего промпта чата, как в команде «чобыло», но не позволяй персоне менять факты или формат радиовыпуска.
- Не используй активную пользовательскую персону как источник фактов или новых событий; она задаёт только стиль подачи. Ведущий остаётся Упупой.
- На этот выпуск редактор выбрал рубрики ниже. Используй только те, для которых реально хватает материала. Не произноси названия рубрик механически: вплетай их как естественные переходы ведущего.
- В середине выпуска один раз пригласи «эксперта». Эксперт — отдельный комический персонаж текущего выпуска, но он НЕ имеет дополнительных знаний. Он может интерпретировать, спорить с ведущим или нелепо оценивать только уже приведённые факты. Эксперт не должен придумывать новые события, цитаты или свойства участников.
- После реплики эксперта ведущий обязательно возвращается и продолжает/заканчивает выпуск.
- Для технического разделения голосов каждую реплику начинай строго с метки «{SPEAKER_HOST}:» или «{SPEAKER_EXPERT}:». Метки не проговариваются. Других меток и заголовков не используй.
{social_rule}{international_rule}- Целевая длительность выпуска — примерно {duration_minutes} мин. Обычно цель — {target_min_words}–{target_max_words} русских слов. Если материала мало, делай короче и не лей воду.
- Никогда не превышай {hard_max_words} слов вместе с метками.

Рубрики этого выпуска:
{_rubrics_prompt(rubrics)}

Заметные по активности участники: {_participant_names(messages)}

Материал чата:
{source_block}
{social_block}{world_block}
Верни только сценарий с метками {SPEAKER_HOST}: / {SPEAKER_EXPERT}:.
"""
    prompt = build_prompt_with_current_chat_prompt(
        chat_id,
        task_prompt,
        task_name="сценарий Радио Упупы",
    )

    logging.info(
        "[radio][script] messages=%s requested_minutes=%s target_words=%s-%s max_words=%s source_chars=%s prompt_context_chars=%s structured_summary=%s social_context=%s world_context=%s rubrics=%s current_prompt=true",
        len(messages),
        duration_minutes,
        target_min_words,
        target_max_words,
        hard_max_words,
        total_context_chars,
        len(source_block),
        use_summary,
        bool(social_context),
        bool(world_context),
        ",".join(name for name, _instruction in rubrics),
    )
    raw_script = await _generate_with_active_model(prompt, chat_id, is_summarization=True)
    script = sanitize_radio_script(raw_script, max_words=hard_max_words)
    if not script:
        raise RuntimeError("Radio script model returned empty text")

    return RadioScript(
        text=script,
        word_count=len(script.split()),
        used_structured_summary=use_summary,
    )
