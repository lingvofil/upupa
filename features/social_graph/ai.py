"""Optional AI wording layer over deterministic social-graph facts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from typing import TYPE_CHECKING

from features.social_graph.analysis import CentralityResult, PersonalSummary

if TYPE_CHECKING:
    from features.social_graph.relationship_history import RelationshipTimelineItem
    from features.social_graph.relationships import RelationshipView


Generator = Callable[[str, str], Awaitable[str]]
_FAILURE_PREFIXES = ("ошибка блят", "произошла ошибка", "я пока не знаю")


async def _default_generator(prompt: str, chat_id: str) -> str:
    from AI.dialog.generation import generate_simple_response

    return await generate_simple_response(prompt, chat_id)


def _clean_response(text: str | None, *, max_chars: int = 420) -> str | None:
    if not text:
        return None
    compact = " ".join(text.split()).strip()
    if not compact or compact.lower().startswith(_FAILURE_PREFIXES):
        return None
    return compact[:max_chars]


def _is_dnd_history_item(item: "RelationshipTimelineItem") -> bool:
    """DnD is a shared activity, not evidence of how the pair relates in chat."""
    return item.event_type == "game_dnd" or item.source in {"dnd_archive", "chronicle:dnd"}


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


async def interpret_relationship(
    view: "RelationshipView",
    chat_id: str,
    *,
    generator: Generator | None = None,
) -> str | None:
    event_titles = "; ".join(event.title for event in view.shared_events[:3]) or "нет отдельных событий"
    prompt = (
        "Ты пишешь одну короткую саркастичную реплику о паре участников Telegram-чата, "
        "используя ТОЛЬКО уже рассчитанные факты ниже. Не анализируй переписку заново, "
        "не придумывай мотивы, чувства, события, цитаты или причины. Допустима ирония, но фактология должна остаться точной.\n"
        f"Пара: {view.user_a_name} ↔ {view.user_b_name}. "
        f"Уровень {view.level}/100 (место {view.rank} из {view.rank_total} пар чата); тип «{view.archetype}»; близость {view.affinity}/100; "
        f"напряжение {view.tension}/100; взаимность {round(view.reciprocity * 100)}%; "
        f"тренд «{view.trend}». Сохранённые совместные события: {event_titles}."
    )
    try:
        return _clean_response(await (generator or _default_generator)(prompt, chat_id), max_chars=360)
    except Exception as exc:
        logging.warning("Relationship AI interpretation failed: %s", exc)
        return None


async def narrate_relationship_history(
    view: "RelationshipView",
    timeline: tuple["RelationshipTimelineItem", ...],
    chat_id: str,
    *,
    generator: Generator | None = None,
) -> str | None:
    narrative_timeline = tuple(item for item in timeline if not _is_dnd_history_item(item))
    if not narrative_timeline:
        return None

    facts = []
    for item in narrative_timeline[-10:]:
        facts.append(
            f"{item.timestamp:%Y-%m-%d} | {item.event_type} | {item.title} | {item.summary} | source={item.source}"
        )
    prompt = (
        "Собери 2–4 связных предложения о развитии отношений двух участников Telegram-чата. "
        "Это НЕ анализ переписки: используй только перечисленные сохранённые факты. "
        "Не добавляй событий, мотивов, эмоций, дат или причин, которых нет во входе. "
        "Сохраняй хронологию, пиши живо и слегка саркастично. Не используй markdown и не перечисляй метрики таблицей.\n"
        f"Пара: {view.user_a_name} ↔ {view.user_b_name}. Сейчас: уровень {view.level}/100, "
        f"тип «{view.archetype}», тренд «{view.trend}».\n"
        "Сохранённые факты:\n" + "\n".join(facts)
    )
    try:
        return _clean_response(await (generator or _default_generator)(prompt, chat_id), max_chars=900)
    except Exception as exc:
        logging.warning("Relationship history AI narrative failed: %s", exc)
        return None


async def name_factions(
    factions,
    names: dict[int, str],
    roles: dict,
    chat_id: str,
    *,
    generator: Generator | None = None,
) -> dict[int, str]:
    """Give each detected faction a short funny name; deterministic facts only."""
    import json
    import re

    if not factions:
        return {}
    blocks = []
    for faction in factions:
        members = []
        for user_id in faction.members[:8]:
            role = roles.get(user_id)
            suffix = f" ({role.role.title.lower()})" if role is not None else ""
            members.append(f"{names.get(user_id, 'участник')}{suffix}")
        blocks.append(
            f"{faction.index}: участники — {', '.join(members)}; "
            f"лидер по связям — {names.get(faction.leader_id, 'участник')}; "
            f"сплочённость {round(faction.cohesion * 100)}%"
        )
    prompt = (
        "Ниже группы участников Telegram-чата, найденные алгоритмом по тому, кто с кем чаще общается. "
        "Придумай каждой группе короткое смешное название в духе партии, клана или секты (2–4 слова). "
        "Опирайся только на имена, роли и сплочённость; не придумывай события, интересы, конфликты и факты. "
        "Без оскорблений по внешности, национальности, полу или здоровью.\n"
        + "\n".join(blocks)
        + '\nВерни ТОЛЬКО JSON-объект вида {"1": "название", "2": "название"}.'
    )
    try:
        raw = await (generator or _default_generator)(prompt, chat_id)
        match = re.search(r"\{.*\}", raw or "", re.DOTALL)
        data = json.loads(match.group(0)) if match else {}
    except Exception as exc:
        logging.warning("Social graph faction naming failed: %s", exc)
        return {}
    result: dict[int, str] = {}
    known = {faction.index for faction in factions}
    for key, value in (data.items() if isinstance(data, dict) else ()):
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        name = " ".join(str(value or "").split()).strip(" «»\"'")
        if index in known and name and not name.lower().startswith(_FAILURE_PREFIXES):
            result[index] = name[:60]
    return result
