"""Pure prompt construction for participant-style imitation."""

from features.lexicon_settings import (
    build_hybrid_style_sample,
    get_frequent_phrases_from_text,
)
from prompts import PROMPTS_DICT


def create_user_style_prompt(messages: list[str], display_name: str) -> str:
    sample_messages = build_hybrid_style_sample(messages)
    all_text = " ".join(sample_messages)
    frequent_words = get_frequent_phrases_from_text(all_text, n=1, top_n=50)
    phrases_2 = get_frequent_phrases_from_text(all_text, n=2, top_n=10)
    phrases_3 = get_frequent_phrases_from_text(all_text, n=3, top_n=10)
    frequent_phrases = phrases_2 + phrases_3

    style_examples = [
        "Последние сообщения (актуальный стиль):",
        *[f"{i}. {msg}" for i, msg in enumerate(sample_messages[:20], 1)],
    ]

    random_examples = sample_messages[20:]
    if random_examples:
        style_examples.extend(
            [
                "",
                "Случайные сообщения из истории (широта лексикона):",
                *[f"{i}. {msg}" for i, msg in enumerate(random_examples, 1)],
            ]
        )

    if frequent_words:
        style_examples.extend(
            [
                "",
                "Часто используемые слова (для анализа, не копировать механически):",
                ", ".join([word for word, _ in frequent_words]),
            ]
        )
    if frequent_phrases:
        style_examples.append("\nХарактерные фразы и обороты (не повторять дословно):")
        for phrase, _ in frequent_phrases:
            style_examples.append(f"- {phrase}")

    return PROMPTS_DICT["участник"].format(
        display_name=display_name,
        style_examples="\n".join(style_examples),
    )
