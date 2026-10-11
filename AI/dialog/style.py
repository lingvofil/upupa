"""Participant style profiling and prompt construction."""

from __future__ import annotations

import collections
import math
import random
import re
import statistics


WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+", re.UNICODE)
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", re.UNICODE)
MAX_STYLE_EXAMPLES = 24
RECENT_STYLE_EXAMPLES = 6
MAX_INTERACTION_EXAMPLES = 8
MAX_BURST_EXAMPLES = 4
STYLE_EXAMPLE_MAX_CHARS = 1200
STYLE_EXAMPLES_CHAR_BUDGET = 7000
INTERACTION_EXAMPLES_CHAR_BUDGET = 7000
LEXICON_SIZE = 40
LEXICON_MIN_COUNT = 3
TURN_SAMPLE_SLOTS = 64
# A model asked for a 13-message series writes 13 three-word lines; real series
# above this size are rare and mostly chat noise. Outlier turns (pastes, long
# rants) are capped so one target never asks for a wall of text.
SERIES_TARGET_MAX_MESSAGES = 5
TURN_TARGET_MAX_WORDS = 150

LENGTH_BUCKETS: tuple[tuple[int, int | None, str], ...] = (
    (0, 3, "0–3"),
    (4, 10, "4–10"),
    (11, 25, "11–25"),
    (26, 60, "26–60"),
    (61, None, "60+"),
)

# Fallback-only filter: with a chat baseline the log-odds score already
# separates personal tics («ну», «чё») from words everybody uses.
RUSSIAN_STOPWORDS = frozenset(
    """
    а без более бы был была были было быть в вам вас весь во вот все всё всего всех вы где да даже для до его ее её
    ей если есть еще ещё же за здесь и из или им их к как ко когда кто ли либо мне мной мы на над надо не него нее неё
    нет ни них но ну о об однако он она они оно от очень по под при с со так также такой там те тем то того тоже той
    только том ты у уже хотя чего чей чем что чтобы чье чья эта эти это этого этой этом этот я меня мой моя мое моё мои
    тебя тебе твой себя себе свой сам сама сами будет буду будут было бывает может можно нужно сейчас тут раз два
    the a an and or of to in is it i you
    """.split()
)


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


def word_count(text: str) -> int:
    return len(_words(text or ""))


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


def _clip(text: str, limit: int = STYLE_EXAMPLE_MAX_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _frequent_phrases(messages: list[str], n: int, top_n: int) -> list[tuple[str, int]]:
    """Count n-grams inside individual messages, never across boundaries.

    N-grams made only of stopwords («я не», «и в») carry no persona signal and
    are skipped.
    """
    counter: collections.Counter[tuple[str, ...]] = collections.Counter()
    seen_long: set[str] = set()
    for message in messages:
        tokens = _words(message)
        # A long message pasted many times is a copypasta (RECURRING PATTERNS
        # territory), not a phrase habit; count each long text only once.
        if len(tokens) > 8:
            key = " ".join(tokens)
            if key in seen_long:
                continue
            seen_long.add(key)
        if len(tokens) < n:
            continue
        counter.update(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))

    # A phrase seen once is not evidence of a speech habit; real examples still
    # preserve one-off wording without pretending it is characteristic.
    return [
        (" ".join(gram), count)
        for gram, count in counter.most_common(top_n * 6)
        if count >= 2 and not all(token in RUSSIAN_STOPWORDS for token in gram)
    ][:top_n]


def count_tokens(messages: list[str]) -> collections.Counter[str]:
    counter: collections.Counter[str] = collections.Counter()
    for message in messages:
        counter.update(_words(message))
    return counter


def distinctive_lexicon(
    user_counts: collections.Counter[str] | dict[str, int],
    baseline_counts: collections.Counter[str] | dict[str, int] | None = None,
    *,
    top_n: int = LEXICON_SIZE,
    min_count: int = LEXICON_MIN_COUNT,
) -> list[str]:
    """Return words this participant uses noticeably more than the rest of the chat.

    With a baseline this is weighted log-odds with an informative Dirichlet
    prior (Monroe et al., 2008): frequent-for-everyone words cancel out, while
    personal slang, mat, nicknames and verbal tics surface. Without a baseline
    it degrades to frequency minus a stopword list.
    """
    user_counts = collections.Counter(user_counts or {})
    candidates = {
        token: count
        for token, count in user_counts.items()
        if count >= min_count and not token.isdigit() and len(token) >= 2
    }
    if not candidates:
        return []

    baseline = collections.Counter(baseline_counts or {})
    if sum(baseline.values()) < 500:
        ranked = sorted(
            (token for token in candidates if token not in RUSSIAN_STOPWORDS),
            key=lambda token: (-candidates[token], token),
        )
        return ranked[:top_n]

    n_user = sum(user_counts.values())
    n_base = sum(baseline.values())
    background = user_counts + baseline
    n_background = n_user + n_base
    alpha0 = 1000.0

    scored: list[tuple[float, str]] = []
    for token, y_user in candidates.items():
        y_base = baseline.get(token, 0)
        alpha = alpha0 * background[token] / n_background
        delta = math.log((y_user + alpha) / (n_user + alpha0 - y_user - alpha)) - math.log(
            (y_base + alpha) / (n_base + alpha0 - y_base - alpha)
        )
        variance = 1.0 / (y_user + alpha) + 1.0 / (y_base + alpha)
        z_score = delta / math.sqrt(variance)
        # Function words need much stronger evidence to count as a personal
        # tic («ну», «чё» do pass; «и», «как» normally do not).
        threshold = 3.3 if token in RUSSIAN_STOPWORDS else 1.96
        if z_score > threshold:
            scored.append((z_score, token))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [token for _score, token in scored[:top_n]]


def compact_turn_samples(turns: list[tuple[int, int]], slots: int = TURN_SAMPLE_SLOTS) -> list[list[int]]:
    """Keep an evenly spaced, length-sorted sample of (words, messages) turns.

    The sample is small enough to persist in chat settings and still preserves
    the long tail of the distribution, which a median alone throws away.
    """
    usable = sorted((int(words), max(1, int(messages))) for words, messages in turns if words > 0)
    if not usable:
        return []
    if len(usable) <= slots:
        return [list(item) for item in usable]
    indexes = [round(i * (len(usable) - 1) / (slots - 1)) for i in range(slots)]
    return [list(usable[index]) for index in indexes]


def describe_length_distribution(word_counts: list[int]) -> str:
    total = len(word_counts)
    shares = []
    for low, high, label in LENGTH_BUCKETS:
        count = sum(1 for value in word_counts if value >= low and (high is None or value <= high))
        shares.append(f"{label}: {_ratio(count, total)}%")
    return "; ".join(shares)


def _length_lines(label: str, word_counts: list[int]) -> list[str]:
    if not word_counts:
        return []
    return [
        (
            f"{label} (в словах): медиана {round(statistics.median(word_counts))}; среднее "
            f"{round(statistics.fmean(word_counts))}; 75-й перцентиль {_percentile(word_counts, 0.75)}; "
            f"90-й перцентиль {_percentile(word_counts, 0.9)}."
        ),
        f"Доли по длине — {describe_length_distribution(word_counts)}.",
    ]


def _select_style_examples(messages: list[str], extra_turns: list[str] | None = None) -> list[str]:
    """Pick examples whose length distribution mirrors the participant's real one.

    The previous version force-added short reactions and took a dozen messages,
    which taught the model that this person is terse even when most of their
    text volume comes from long messages. Now the sample is length-stratified,
    always includes the freshest messages and fits a character budget.
    """
    filtered = [message.strip() for message in messages if is_participant_style_message(message)]
    if not filtered:
        return []

    recent = filtered[-RECENT_STYLE_EXAMPLES:]
    older = filtered[: -RECENT_STYLE_EXAMPLES] if len(filtered) > RECENT_STYLE_EXAMPLES else []

    chosen_indexes: set[int] = set()
    slots = max(0, MAX_STYLE_EXAMPLES - len(recent))
    if older and slots:
        by_length = sorted(range(len(older)), key=lambda index: (word_count(older[index]), index))
        if len(by_length) <= slots:
            chosen_indexes.update(by_length)
        else:
            step_positions = [round(i * (len(by_length) - 1) / (slots - 1)) for i in range(slots)] if slots > 1 else [
                len(by_length) // 2
            ]
            chosen_indexes.update(by_length[position] for position in step_positions)

    ordered = [older[index] for index in sorted(chosen_indexes)] + recent
    ordered.extend(turn for turn in (extra_turns or [])[-MAX_BURST_EXAMPLES:] if turn.strip())

    selected: list[str] = []
    budget = STYLE_EXAMPLES_CHAR_BUDGET
    for message in ordered:
        clipped = _clip(message)
        if clipped in selected:
            continue
        if selected and len(clipped) > budget:
            continue
        selected.append(clipped)
        budget -= len(clipped)
    return selected


def build_style_fingerprint(messages: list[str], stats: dict | None = None) -> str:
    """Describe measurable Telegram writing habits."""
    filtered = [message.strip() for message in messages if is_participant_style_message(message)]
    if not filtered:
        return "Недостаточно данных для устойчивого стилевого профиля."

    stats = stats or {}
    total = len(filtered)
    message_lengths = [int(value) for value in stats.get("message_lengths") or []] or [
        word_count(message) for message in filtered
    ]
    turn_samples = stats.get("turn_samples") or []

    starts = [_first_alpha(message) for message in filtered]
    alpha_starts = [char for char in starts if char]
    lowercase_starts = sum(char.islower() for char in alpha_starts)

    lines = [f"Сообщений в выборке: {stats.get('message_count') or total}."]
    lines.extend(_length_lines("Длина одного сообщения", message_lengths))
    lines.append(
        f"Очень коротких сообщений (0–3 слова) {_ratio(sum(v <= 3 for v in message_lengths), len(message_lengths))}%; "
        f"длинных (26+ слов) {_ratio(sum(v >= 26 for v in message_lengths), len(message_lengths))}%."
    )

    multi_share = stats.get("multi_message_turn_share")
    if turn_samples and multi_share is not None:
        turn_words = [int(words) for words, _messages in turn_samples]
        lines.append(
            "Серии: человек часто пишет несколькими сообщениями подряд — "
            f"{multi_share}% его реплик состоят из 2+ сообщений, в среднем "
            f"{stats.get('avg_messages_per_turn', 1)} сообщения на реплику."
        )
        lines.extend(_length_lines("Суммарный объём одной реплики (вся серия)", turn_words))

    lines.extend(
        [
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
                f"многоточие {_ratio(sum(('...' in message or '…' in message) for message in filtered), total)}%; "
                f"запятые в среднем {round(sum(message.count(',') for message in filtered) / total, 1)} на сообщение."
            ),
            (
                "Формат: "
                f"многострочных сообщений {_ratio(sum(chr(10) in message for message in filtered), total)}%; "
                f"со смайлами/эмодзи {_ratio(sum(bool(EMOJI_RE.search(message)) for message in filtered), total)}%."
            ),
        ]
    )

    trigrams = _frequent_phrases(filtered, n=3, top_n=6)
    bigrams = [
        (phrase, count)
        for phrase, count in _frequent_phrases(filtered, n=2, top_n=10)
        # Drop a bigram that only exists as part of an already listed trigram.
        if not any(phrase in tri and tri_count >= count * 0.8 for tri, tri_count in trigrams)
    ]
    phrases = bigrams + trigrams
    if phrases:
        lines.append("Повторяющиеся обороты: " + "; ".join(f"{phrase} ({count})" for phrase, count in phrases) + ".")

    return "\n".join(lines)


def create_user_style_prompt(
    messages: list[str],
    display_name: str,
    interaction_examples: list[str] | None = None,
    recurring_examples: list[str] | None = None,
    stats: dict | None = None,
) -> str:
    """Create a participant persona from style, lexicon and real conversational behavior."""
    stats = stats or {}
    filtered = [message.strip() for message in messages if is_participant_style_message(message)]
    fingerprint = build_style_fingerprint(filtered, stats)

    lexicon = list(stats.get("lexicon") or []) or distinctive_lexicon(count_tokens(filtered))
    lexicon_text = ", ".join(lexicon)

    examples = _select_style_examples(filtered, stats.get("burst_examples"))
    examples_text = "\n".join(f"{index}. {message}" for index, message in enumerate(examples, 1))

    interaction_pool = [item.strip() for item in (interaction_examples or []) if item.strip()]
    if len(interaction_pool) <= MAX_INTERACTION_EXAMPLES:
        interactions = interaction_pool
    else:
        older_slots = MAX_INTERACTION_EXAMPLES // 2
        interactions = interaction_pool[:older_slots] + interaction_pool[-(MAX_INTERACTION_EXAMPLES - older_slots):]
    kept_interactions: list[str] = []
    budget = INTERACTION_EXAMPLES_CHAR_BUDGET
    for item in interactions:
        if kept_interactions and len(item) > budget:
            continue
        kept_interactions.append(item)
        budget -= len(item)
    interactions_text = "\n\n".join(f"{index}. {item}" for index, item in enumerate(kept_interactions, 1))

    recurring = [item.strip() for item in (recurring_examples or []) if item.strip()]
    recurring_text = "\n".join(f"- {item}" for item in recurring)

    return (
        f"Ты пишешь в Telegram-чат от лица участника {display_name}. Твоя задача — создавать НОВЫЕ сообщения, "
        "которые невозможно отличить от его/её настоящих: тот же объём, та же лексика, тот же ритм, та же манера "
        "реагировать. Цитаты не выдавай за новый текст.\n\n"
        "[STYLE PROFILE]\n"
        f"{fingerprint}\n"
        "[/STYLE PROFILE]\n\n"
        "[ХАРАКТЕРНАЯ ЛЕКСИКА]\n"
        f"{lexicon_text or 'Недостаточно данных.'}\n"
        "Это слова, которые этот человек использует заметно чаще остальных участников чата "
        "(сленг, мат, словечки, слова-паразиты, любимые темы).\n"
        "[/ХАРАКТЕРНАЯ ЛЕКСИКА]\n\n"
        "[STYLE EXAMPLES]\n"
        f"{examples_text or 'Нет подходящих примеров.'}\n"
        "[/STYLE EXAMPLES]\n\n"
        "[INTERACTION EXAMPLES]\n"
        f"{interactions_text or 'Нет надёжных контекстных примеров.'}\n"
        "[/INTERACTION EXAMPLES]\n\n"
        "[RECURRING PATTERNS]\n"
        f"{recurring_text or 'Нет достаточно повторяющихся коротких реплик.'}\n"
        "[/RECURRING PATTERNS]\n\n"
        "Правила имитации:\n"
        "- Объём — главный признак узнаваемости. Распределение длины в STYLE PROFILE — реальное, а не потолок. "
        "Каждый ход получает отдельную цель в блоке [ДЛИНА ОТВЕТА] — попадай в неё. Не сжимай, не резюмируй и не "
        "«причёсывай» мысль: если человек обычно пишет развёрнуто, пиши развёрнуто — с его отступлениями, "
        "уточнениями, повторами, рассуждениями вслух и переходами на смежные темы.\n"
        "- Серию из нескольких сообщений пиши только когда [ДЛИНА ОТВЕТА] её просит: каждое сообщение серии с новой "
        "строки, без нумерации и маркеров. В остальных случаях — одно сообщение без переносов строк.\n"
        "- Короткие реакции — тоже часть стиля, но только в той доле, в какой они реально встречаются в распределении.\n"
        "- Лексика: всегда предпочитай слова из ХАРАКТЕРНОЙ ЛЕКСИКИ и STYLE EXAMPLES нейтральным синонимам. "
        "В обычном ответе естественно используй несколько таких слов (в длинном — больше), но только к месту, "
        "не списком и не все подряд. Копируй мат, сленг, сокращения, орфографию, ошибки и пунктуацию человека; "
        "не исправляй и не облагораживай его речь.\n"
        "- RECURRING PATTERNS — это доказательство устойчивых присказок, просьб и мотивов, а не обязательный набор фраз "
        "для каждого ответа. Используй такой паттерн только когда текущий контекст действительно к нему ведёт.\n"
        "- Не повторяй одну и ту же фирменную фразу в соседних ответах только ради узнаваемости. После её использования "
        "переключайся на другие характерные реакции, лексику и темы. Повтор уместен, если собеседник сам явно подхватил "
        "эту фразу или просит её повторить.\n"
        "- INTERACTION EXAMPLES важнее усреднённой вежливости: они показывают, на что человек цепляется, что игнорирует, "
        "как отказывает, подкалывает, просит, спорит или меняет тему, и сколько он при этом пишет. Не превращай ответ "
        "в нейтральное поддержание беседы, если реальные примеры показывают более характерную реакцию.\n"
        "- Не копируй целиком уникальные старые реплики и не склеивай ответ из кусков. Но короткие устойчивые присказки, "
        "повторяющиеся просьбы, междометия и фирменные формулировки можно естественно повторять, если они действительно "
        "встречаются неоднократно.\n"
        "- STYLE EXAMPLES показывают манеру речи. Не считай случайные факты из них вечными убеждениями человека.\n"
        "- Повторяющиеся темы, просьбы и поведенческие привычки из примеров можно использовать как часть образа, но не "
        "превращай их в новые биографические факты. Конкретные взгляды, биографию и прошлый опыт можно приписывать человеку "
        "только когда они подтверждены отдельно переданным SEMANTIC MEMORY.\n"
        f"- Отвечай от лица {display_name} естественно для обычного группового Telegram-чата: без кавычек вокруг ответа, "
        "без подписи именем и без пояснений."
    )


def pick_turn_length_target(
    turn_samples: list[list[int]] | list[tuple[int, int]] | None,
    current_message: str = "",
    rng: random.Random | None = None,
) -> tuple[int, int] | None:
    """Sample (words, messages) for this reply from the participant's real turns.

    A direct question or a long incoming message nudges the sample toward the
    upper part of the distribution; otherwise every real turn length is equally
    likely, so the bot reproduces the long tail instead of collapsing to the mode.
    """
    samples = sorted((int(words), max(1, int(messages))) for words, messages in (turn_samples or []) if int(words) > 0)
    if not samples:
        return None
    rng = rng or random
    incoming_words = word_count(current_message)
    low = 0.0
    if "?" in (current_message or "") or incoming_words >= 15:
        low = 0.3
    position = low + rng.random() * (1.0 - low)
    index = min(len(samples) - 1, int(position * len(samples)))
    words, messages = samples[index]
    return min(words, TURN_TARGET_MAX_WORDS), min(messages, SERIES_TARGET_MAX_MESSAGES, max(1, words))


def format_turn_length_directive(target: tuple[int, int] | None) -> str:
    if not target:
        return ""
    words, messages = target
    if words <= 3:
        body = (
            f"Этот ход — короткая реакция: примерно {words} слов(а), как у участника в его самых коротких репликах."
        )
    else:
        low = max(1, round(words * 0.7))
        high = max(low + 1, round(words * 1.3))
        body = (
            f"Целевой объём этого ответа: около {words} слов (нормально {low}–{high}). Для этого участника такой объём "
            "обычен. Не укорачивай ответ ради лаконичности; если мысли не хватает — развивай её так, как развивал бы он: "
            "детали, личное отношение, отступления, подколы, встречные вопросы."
        )
    if messages >= 2:
        per_message = max(1, round(words / messages))
        body += (
            f" Оформи как серию из {messages} сообщений подряд: ровно {messages} строк, каждая строка уйдёт отдельным "
            f"сообщением, примерно по {per_message} слов в каждой. Не дроби мысль на большее число строк."
        )
    else:
        body += " Это одно сообщение: пиши его одной строкой, без переносов строк."
    return f"\n[ДЛИНА ОТВЕТА]\n{body}\n[/ДЛИНА ОТВЕТА]\n"
