"""Natural social-graph context for summaries and Radio Upupa."""

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


def _pair_observation(edge, names: dict[int, str]) -> str:
    a = _clean_name(names, edge.user_a)
    b = _clean_name(names, edge.user_b)
    total = max(float(edge.total_weight), 1.0)
    imbalance = abs(float(edge.a_to_b) - float(edge.b_to_a)) / total
    if imbalance >= 0.55:
        if edge.a_to_b > edge.b_to_a:
            return f"{a} особенно часто обращался к {b}."
        return f"{b} особенно часто обращался к {a}."
    return f"{a} и {b} заметно чаще других взаимодействовали друг с другом."


def _format_context(interactions, names: dict[int, str], *, purpose: str) -> str:
    edges = aggregate_edges(interactions)
    if not edges:
        return ""

    strongest = sorted(edges, key=lambda edge: (-edge.total_weight, edge.user_a, edge.user_b))[:MAX_PAIRS]
    central = rank_central_participants(edges, limit=MAX_CENTRAL)

    lines = [
        "СОЦИАЛЬНЫЕ НАБЛЮДЕНИЯ ЗА ТОТ ЖЕ ПЕРИОД:",
        "Это факты о реплаях, упоминаниях и реакциях. Не называй их дружбой, конфликтом, романом или враждой без подтверждения из сообщений.",
    ]
    lines.extend(f"- {_pair_observation(edge, names)}" for edge in strongest)

    if central:
        central_names = [_clean_name(names, item.user_id) for item in central]
        if len(central_names) == 1:
            lines.append(f"- В центре общения чаще оказывался {central_names[0]}.")
        else:
            lines.append(f"- В центре разных веток общения чаще оказывались {', '.join(central_names)}.")

    if purpose == "radio":
        lines.append(
            "В выпуске обязательно естественно упомяни хотя бы одно из этих наблюдений, если оно не дублирует уже рассказанный эпизод. "
            "Не произноси слова «соцграф», «метрика», «вес связи», «betweenness» и не называй численные показатели."
        )
    else:
        lines.append(
            "В сводке обязательно естественно вплети одно-два этих наблюдения, чтобы было заметно, кто с кем реально взаимодействовал. "
            "Не произноси слова «соцграф», «метрика», «вес связи», «betweenness» и не называй численные показатели или количество сообщений."
        )
    return "\n".join(lines)


async def _build_window_social_context(
    chat_id: int,
    since: datetime,
    *,
    purpose: str,
) -> str:
    if not social_service.is_social_graph_enabled(chat_id):
        return ""
    try:
        interactions, names = await asyncio.to_thread(
            social_service._repository().load_graph,
            int(chat_id),
            _utc(since),
        )
    except Exception:
        return ""
    return _format_context(interactions, names, purpose=purpose)


async def build_summary_social_context(message, *, catchup: bool) -> str:
    """Return natural social facts aligned to the same logical summary window."""
    user = getattr(message, "from_user", None)
    user_id = user.id if user and not getattr(message, "sender_chat", None) else None
    now = datetime.now(timezone.utc)
    since = _summary_since(str(message.chat.id), user_id, catchup=catchup, now=now)
    return await _build_window_social_context(int(message.chat.id), since, purpose="summary")


async def build_radio_social_context(
    chat_id: int | str,
    period_hours: int,
    *,
    now: datetime | None = None,
) -> str:
    """Return social observations for exactly the period used by a Radio episode."""
    current = _utc(now or datetime.now(timezone.utc))
    since = current - timedelta(hours=max(1, int(period_hours)))
    return await _build_window_social_context(int(chat_id), since, purpose="radio")
