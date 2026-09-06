"""Factual social-graph context for ``чобыло`` and personal catch-up summaries."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from core.history_store import get_history_repository
from core.paths import USER_MESSAGES_LOG_PATH
from features.social_graph.analysis import aggregate_edges, rank_central_participants
from features.social_graph import service as social_service


MAX_PAIRS = 4
MAX_CENTRAL = 3


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _summary_since(chat_id: str, user_id: int | None, *, catchup: bool, now: datetime) -> datetime:
    fallback = now - timedelta(hours=12)
    if not catchup or user_id is None:
        return fallback
    history = get_history_repository(USER_MESSAGES_LOG_PATH)
    if history is None:
        return fallback
    _through_id, previous = history.summary_boundary(chat_id, user_id)
    if not previous:
        return fallback
    try:
        return _utc(datetime.fromisoformat(str(previous["requested_at"])))
    except (KeyError, TypeError, ValueError):
        return fallback


def _clean_name(names: dict[int, str], user_id: int) -> str:
    name = str(names.get(user_id) or f"участник {user_id}").strip()
    return name.replace("\n", " ")[:80]


def _format_context(interactions, names: dict[int, str], since: datetime, now: datetime) -> str:
    edges = aggregate_edges(interactions)
    if not edges:
        return "Соцграф за период не зафиксировал реплаев, упоминаний или реакций между участниками."

    strongest = sorted(edges, key=lambda edge: (-edge.total_weight, edge.user_a, edge.user_b))[:MAX_PAIRS]
    central = rank_central_participants(edges, limit=MAX_CENTRAL)
    hours = max(1, round((now - since).total_seconds() / 3600))

    lines = [
        f"СОЦГРАФ ЗА ТОТ ЖЕ ПЕРИОД (примерно {hours} ч.):",
        "Вес взаимодействий: реплай=3, упоминание=2, реакция=1. Это структура общения, а не оценка людей.",
        "Самые сильные пары:",
    ]
    for edge in strongest:
        a = _clean_name(names, edge.user_a)
        b = _clean_name(names, edge.user_b)
        lines.append(
            f"- {a} ↔ {b}: сила {edge.total_weight:g}; направления {a}→{b} {edge.a_to_b:g}, {b}→{a} {edge.b_to_a:g}."
        )

    if central:
        lines.append("Структурно заметные участники:")
        for item in central:
            lines.append(
                f"- {_clean_name(names, item.user_id)}: сила связей {item.weighted_degree:g}, "
                f"разных связей {item.unique_neighbors}, betweenness {item.betweenness:.2f}."
            )

    asymmetric = [
        edge for edge in edges
        if edge.total_weight >= 3 and abs(edge.a_to_b - edge.b_to_a) / edge.total_weight >= 0.55
    ]
    if asymmetric:
        edge = max(asymmetric, key=lambda item: abs(item.a_to_b - item.b_to_a))
        a = _clean_name(names, edge.user_a)
        b = _clean_name(names, edge.user_b)
        direction = f"{a} чаще обращался к {b}" if edge.a_to_b > edge.b_to_a else f"{b} чаще обращался к {a}"
        lines.append(f"Заметная асимметрия: {direction}.")

    lines.append(
        "В сводке можно коротко отметить эти реальные паттерны, если они помогают понять происходящее. "
        "Не называй связь дружбой, конфликтом, романом или враждой без подтверждения из самих сообщений."
    )
    return "\n".join(lines)


async def build_summary_social_context(message, *, catchup: bool) -> str:
    """Return social facts aligned to the same logical summary window."""
    if not social_service.is_social_graph_enabled(message.chat.id):
        return ""
    user = getattr(message, "from_user", None)
    user_id = user.id if user and not getattr(message, "sender_chat", None) else None
    now = datetime.now(timezone.utc)
    since = _summary_since(str(message.chat.id), user_id, catchup=catchup, now=now)
    try:
        interactions, names = await asyncio.to_thread(
            social_service._repository().load_graph,
            int(message.chat.id),
            since,
        )
    except Exception:
        return ""
    return _format_context(interactions, names, since, now)
