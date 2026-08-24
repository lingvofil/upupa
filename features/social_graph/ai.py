"""Optional AI wording layer over deterministic social-graph facts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging

from features.social_graph.analysis import CentralityResult, PersonalSummary


Generator = Callable[[str, str], Awaitable[str]]
_FAILURE_PREFIXES = ("ошибка блят", "произошла ошибка", "я пока не знаю")


async def _default_generator(prompt: str, chat_id: str) -> str:
    from AI.dialog.generation import generate_simple_response

    return await generate_simple_response(prompt, chat_id)


def _clean_response(text: str | None) -> str | None:
    if not text:
        return None
    compact = " ".join(text.split()).strip()
    if not compact or compact.lower().startswith(_FAILURE_PREFIXES):
        return None
    return compact[:420]


async def interpret_personal_summary(
    summary: PersonalSummary,
    names: dict[int, str],
    chat_id: str,
    *,
    generator: Generator | None = None,
) -> str | None:
    facts = []
    for connection in summary.strongest_mutual[:3]:
        facts.append(
            f"{names.get(connection.user_id, 'участник')}: туда {connection.outgoing:.1f}, "
            f"обратно {connection.incoming:.1f}, взаимность {connection.reciprocity:.2f}"
        )
    if summary.strongest_asymmetry:
        item = summary.strongest_asymmetry
        facts.append(
            f"асимметрия с {names.get(item.user_id, 'участник')}: туда {item.outgoing:.1f}, "
            f"обратно {item.incoming:.1f}"
        )

    prompt = (
        "Ты кратко интерпретируешь уже рассчитанную статистику социального графа Telegram-чата. "
        "Не пересчитывай граф, не придумывай отношения, эмоции, коалиции или игровые титулы. "
        "Сформулируй 1–2 нейтральных предложения человеческим языком.\n"
        f"Всего исходящих весов: {summary.total_outgoing:.1f}; входящих: {summary.total_incoming:.1f}; "
        f"разных связей: {summary.distinct_connections}.\n"
        + ("; ".join(facts) if facts else "Двусторонних сильных связей пока мало.")
    )
    try:
        return _clean_response(await (generator or _default_generator)(prompt, chat_id))
    except Exception as exc:
        logging.warning("Social graph personal AI interpretation failed: %s", exc)
        return None


async def interpret_centrality(
    top: CentralityResult,
    name: str,
    chat_id: str,
    *,
    generator: Generator | None = None,
) -> str | None:
    prompt = (
        "Ты кратко объясняешь уже рассчитанную центральность участника Telegram-чата. "
        "Не меняй метрики, не придумывай психологию, дружбу, коалиции или игровые титулы. "
        "Одно нейтральное предложение.\n"
        f"Участник: {name}. Сила всех связей: {top.weighted_degree:.1f}; "
        f"разных соседей: {top.unique_neighbors}; сильных связей: {top.strong_neighbors}; "
        f"нормализованная betweenness: {top.betweenness:.3f}."
    )
    try:
        return _clean_response(await (generator or _default_generator)(prompt, chat_id))
    except Exception as exc:
        logging.warning("Social graph centrality AI interpretation failed: %s", exc)
        return None
