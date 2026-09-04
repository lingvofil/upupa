"""Participant style profiling and prompt construction."""

from __future__ import annotations

import collections
import math
import re
import statistics


WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+", re.UNICODE)
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", re.UNICODE)
MAX_STYLE_EXAMPLES = 12
RECENT_STYLE_EXAMPLES = 6


def is_participant_style_message(text: str) -> bool:
    """Return whether a Telegram message is useful for participant imitation.

    Short reactions are intentionally preserved: for messenger style, messages
    such as «ага», «ору», «бля» or a single emoji can be highly distinctive.
    Only empty messages and slash commands are excluded.
    """
    if not text:
        return False
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith("/")


def _words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def _ratio(part: int, total: int) -> int:
    if total <= 0:
        return 0
    return round(part * 100 / total)


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _first_alpha(text: str) -> str:
    for char in text:
        if char.isalpha():
            return char
    return ""


def _frequent_phrases(messages: list[str], n: int, top_n: int) -> list[tuple[str, int]]:
    """Count n-grams inside individual messages, never across boundaries."""
    counter: collections.Counter[tuple[str, ...]] = collections.Counter()
    for message in messages:
        tokens = _words(message)
        if len(tokens) < n:
            continue
        counter.update(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))

    # A phrase seen once is not evidence of a speech habit; real examples still
    # preserve one-off wording without pretending it is characteristic.
    return [
        (" ".join(gram), count)
        for gram, count in counter.most_common(top_n * 3)
        if count >= 2
    ][:top_n]


def _select_style_examples(messages: list[str]) -> list[str]:
    """Build a compact deterministic recent + historical + short-reaction sample."""
    filtered = [message.strip() for message in messages if is_participant_style_message(message)]
    if not filtered:
        return []

    recent = filtered[-RECENT_STYLE_EXAMPLES:]
    older = filtered[: -RECENT_STYLE_EXAMPLES] if len(filtered) > RECENT_STYLE_EXAMPLES else []

    selected: list[str] = []

    def add(message: str) -> None:
        if message and message not in selected and len(selected) < MAX_STYLE_EXAMPLES:
            selected.append(message)

    # Preserve short reactions explicitly instead of filtering them out.
    short_older = [message for message in older if len(_words(message)) <= 3]
    for message in short_older[-2:]:
        add(message)

    slots = MAX_STYLE_EXAMPLES - len(recent) - len(selected)
    if slots > 0 and older:
        if slots == 1:
            indexes = [len(older) // 2]
        else:
            indexes = [round(i * (len(older) - 1) / (slots - 1)) for i in range(slots)]
        for index in indexes:
            add(older[index])

    for message in recent:
        add(message)

    # If deduplication left free slots, fill them from newest to oldest.
    if len(selected) < MAX_STYLE_EXAMPLES:
        for message in reversed(filtered):
            add(message)
            if len(selected) >= MAX_STYLE_EXAMPLES:
                break

    return selected


def build_style_fingerprint(messages: list[str]) -> str:
    """Describe measurable Telegram writing habits without generic stopword removal."""
    filtered = [message.strip() for message in messages if is_participant_style_message(message)]
    if not filtered:
        return "Недостаточно данных для устойчивого стилевого профиля."

    word_counts = [len(_words(message)) for message in filtered]
    total = len(filtered)

    starts = [_first_alpha(message) for message in filtered]
    alpha_starts = [char for char in starts if char]
    lowercase_starts = sum(char.islower() for char in alpha_starts)

    token_counter: collections.Counter[str] = collections.Counter()
    for message in filtered:
        token_counter.update(_words(message))

    frequent_tokens = [token for token, _count in token_counter.most_common(15)]
    phrases_2 = _frequent_phrases(filtered, n=2, top_n=8)
    phrases_3 = _frequent_phrases(filtered, n=3, top_n=5)

    lines = [
        f"Сообщений в выборке: {total}.",
        (
            "Длина: медиана "
            f"{round(statistics.median(word_counts)) if word_counts else 0} слов; "
            f"75-й перцентиль {_percentile(word_counts, 0.75)} слов; "
            f"очень коротких (0–3 слова) {_ratio(sum(count <= 3 for count in word_counts), total)}%."
        ),
        (
            "Регистр: "
            f"со строчной буквы начинаются {_ratio(lowercase_starts, len(alpha_starts))}% сообщений "
            "с буквенным началом."
        ),
        (
            "Пунктуация: "
            f"точка в конце {_ratio(sum(message.endswith('.') for message in filtered), total)}%; "
            f"вопросительный знак {_ratio(sum('?' in message for message in filtered), total)}%; "
            f"восклицательный знак {_ratio(sum('!' in message for message in filtered), total)}%; "
            f"многоточие {_ratio(sum(('...' in message or '…' in message) for message in filtered), total)}%."
        ),
        (
            "Формат: "
            f"многострочных сообщений {_ratio(sum(chr(10) in message for message in filtered), total)}%; "
            f"со смайлами/эмодзи {_ratio(sum(bool(EMOJI_RE.search(message)) for message in filtered), total)}%."
        ),
    ]

    if frequent_tokens:
        lines.append("Частые слова и речевые маркеры (включая служебные слова): " + ", ".join(frequent_tokens) + ".")

    repeated_phrases = phrases_2 + phrases_3
    if repeated_phrases:
        lines.append(
            "Повторяющиеся обороты: "
            + "; ".join(f"{phrase} ({count})" for phrase, count in repeated_phrases)
            + "."
        )

    return "\n".join(lines)


def create_user_style_prompt(messages: list[str], display_name: str) -> str:
    """Create a compact participant persona from measurable style + real examples."""
    filtered = [message.strip() for message in messages if is_participant_style_message(message)]
    fingerprint = build_style_fingerprint(filtered)
    examples = _select_style_examples(filtered)
    examples_text = "\n".join(f"{index}. {message}" for index, message in enumerate(examples, 1))

    return (
        "Ты — имитатор манеры общения участника Telegram-чата. Твоя задача — создавать НОВЫЕ сообщения "
        f"в стиле {display_name}, сохраняя его/её реальную манеру письма, но не выдавая цитаты за новый текст.\n\n"
        "[STYLE PROFILE]\n"
        f"{fingerprint}\n"
        "[/STYLE PROFILE]\n\n"
        "[STYLE EXAMPLES]\n"
        f"{examples_text or 'Нет подходящих примеров.'}\n"
        "[/STYLE EXAMPLES]\n\n"
        "Правила имитации:\n"
        "- Копируй статистически заметные привычки: типичную длину, регистр, пунктуацию, мат, сленг, "
        "эмодзи, междометия, ошибки, ритм и степень подробности.\n"
        "- Не навязывай универсальный лимит длины: короткий или длинный ответ выбирай по профилю и контексту.\n"
        "- Короткие реакции — полноценная часть стиля. Если человек часто отвечает одним-двумя словами, делай так же.\n"
        "- Не повторяй STYLE EXAMPLES и найденные старые сообщения дословно, не цитируй логи и не склеивай ответ из кусков.\n"
        "- STYLE EXAMPLES показывают только манеру речи. Не считай случайные факты из них вечными убеждениями человека.\n"
        "- Конкретные взгляды, предпочтения, биографические факты и прошлый опыт можно приписывать человеку только когда "
        "они подтверждены отдельно переданным SEMANTIC MEMORY. Если такой памяти нет, имитируй только стиль и не выдумывай позицию.\n"
        f"- Отвечай от лица {display_name} естественно для обычного группового Telegram-чата."
    )
