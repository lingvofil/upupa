"""«Политическая карта» чата для Радио Упупы: фракции, роли и отношения пар.

Окно выпуска (24ч–7д) решает, *о ком* говорить: в контекст попадают только
участники, которые реально общались в этом окне.  Сами лагеря, роли и ярлыки
отношений берутся из долгосрочной структуры соцграфа (по умолчанию 30 дней) —
это тот же «канон», который бот показывает в досье и карте фракций.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Iterable, Mapping, Sequence

from features.social_graph.analysis import GraphEdge, aggregate_edges


MAX_FACTIONS = 4
MAX_FACTION_MEMBERS = 6
MAX_CROSS_CONTACTS = 2
MAX_ROLES = 4
MAX_RELATIONSHIPS = 4
MAX_UNAFFILIATED = 4
FACTION_NAME_TIMEOUT_SECONDS = 20.0

# «Собеседник» и «одиночка» — фон, а не повод для эфира.
RADIO_ROLE_KEYS = ("hub", "bridge", "instigator", "magnet", "pinger", "reactor")
STABLE_TREND = "отношения стабильны"
BORING_ARCHETYPES = frozenset({"периодически пересекаются", "регулярные собеседники"})


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clean(value: str | None) -> str:
    return " ".join(str(value or "Участник").split())[:80]


def _name(names: Mapping[int, str], user_id: int) -> str:
    return _clean(names.get(user_id) or f"участник {user_id}")


def _active_ids(window_edges: Iterable[GraphEdge]) -> set[int]:
    active: set[int] = set()
    for edge in window_edges:
        if edge.total_weight > 0:
            active.update((edge.user_a, edge.user_b))
    return active


def _faction_lines(structure, faction_titles: Mapping[int, str], names: Mapping[int, str], active: set[int]) -> list[str]:
    factions = structure.factions.factions
    if len(factions) < 2:
        return []
    relevant = [faction for faction in factions if active.intersection(faction.members)]
    if not relevant:
        return []

    lines = [f"Лагеря чата (сложились за последние {structure.period_days} дней общения):"]
    for faction in relevant[:MAX_FACTIONS]:
        members = sorted(faction.members, key=lambda user_id: (user_id not in active, user_id != faction.leader_id))
        shown = ", ".join(_name(names, user_id) for user_id in members[:MAX_FACTION_MEMBERS])
        extra = len(members) - MAX_FACTION_MEMBERS
        if extra > 0:
            shown += f" и ещё {extra}"
        title = _clean(faction_titles.get(faction.index) or f"Фракция {faction.index}")
        lines.append(f"- «{title}»: лидер {_name(names, faction.leader_id)}; состав: {shown}.")

    liaison = structure.factions.liaison_id
    if liaison is not None and liaison in active:
        lines.append(f"- Главный связной между лагерями — {_name(names, liaison)}.")
    loners = [user_id for user_id in structure.factions.unaffiliated if user_id in active]
    if loners:
        lines.append("- Вне лагерей держатся: " + ", ".join(_name(names, u) for u in loners[:MAX_UNAFFILIATED]) + ".")
    return lines


def _cross_contact_lines(
    structure,
    faction_titles: Mapping[int, str],
    names: Mapping[int, str],
    window_edges: Sequence[GraphEdge],
) -> list[str]:
    membership = structure.factions.membership
    if len(structure.factions.factions) < 2:
        return []
    cross = [
        edge
        for edge in window_edges
        if edge.total_weight > 0
        and membership.get(edge.user_a) is not None
        and membership.get(edge.user_b) is not None
        and membership[edge.user_a] != membership[edge.user_b]
    ]
    cross.sort(key=lambda edge: (-edge.total_weight, edge.user_a, edge.user_b))
    lines = []
    for edge in cross[:MAX_CROSS_CONTACTS]:
        left = _clean(faction_titles.get(membership[edge.user_a]) or f"Фракция {membership[edge.user_a]}")
        right = _clean(faction_titles.get(membership[edge.user_b]) or f"Фракция {membership[edge.user_b]}")
        lines.append(
            f"- В этот период {_name(names, edge.user_a)} («{left}») заметно взаимодействовал "
            f"с {_name(names, edge.user_b)} («{right}») — контакт через линию фронта."
        )
    if lines:
        lines.insert(0, "Межлагерные контакты за период выпуска:")
    return lines


def _role_lines(structure, names: Mapping[int, str], active: set[int]) -> list[str]:
    picked = [
        role
        for user_id, role in structure.roles.items()
        if user_id in active and role.role.key in RADIO_ROLE_KEYS
    ]
    picked.sort(key=lambda role: (RADIO_ROLE_KEYS.index(role.role.key), -role.stats.strength, role.user_id))
    if not picked:
        return []
    lines = ["Роли участников:"]
    lines.extend(
        f"- {_name(names, role.user_id)} — {role.role.title.lower()}: {role.role.blurb}."
        for role in picked[:MAX_ROLES]
    )
    return lines


def _relationship_lines(relationships: Iterable, active: set[int]) -> list[str]:
    candidates = []
    for view in relationships:
        if view.user_a_id not in active or view.user_b_id not in active:
            continue
        moving = view.trend != STABLE_TREND
        notable = view.archetype not in BORING_ARCHETYPES
        if not (moving or notable):
            continue
        candidates.append((0 if moving and notable else 1 if moving else 2, -view.level, view))
    candidates.sort(key=lambda item: (item[0], item[1], item[2].user_a_id, item[2].user_b_id))
    if not candidates:
        return []
    lines = ["Отношения пар (ярлыки из досье Упупы):"]
    for _priority, _level, view in candidates[:MAX_RELATIONSHIPS]:
        line = f"- {_clean(view.user_a_name)} и {_clean(view.user_b_name)}: «{view.archetype}»"
        if view.trend != STABLE_TREND:
            line += f", сейчас — {view.trend}"
        lines.append(line + ".")
    return lines


def format_radio_structure(
    structure,
    faction_titles: Mapping[int, str],
    window_edges: Sequence[GraphEdge],
    window_names: Mapping[int, str],
    relationships: Iterable = (),
) -> str:
    """Pure formatter: only people active in the episode window make it to air."""
    active = _active_ids(window_edges)
    if not active:
        return ""
    names = {**dict(window_names), **dict(structure.names)}
    sections = [
        _faction_lines(structure, faction_titles, names, active),
        _cross_contact_lines(structure, faction_titles, names, window_edges),
        _role_lines(structure, names, active),
        _relationship_lines(relationships, active),
    ]
    sections = [section for section in sections if section]
    if not sections:
        return ""
    lines = ["ПОЛИТИЧЕСКАЯ КАРТА ЧАТА:"]
    for section in sections:
        lines.extend(section)
    lines.append(
        "Это закрепившийся канон Упупы: названия лагерей, роли и ярлыки пар можно произносить как есть и обыгрывать "
        "в духе политической хроники. Но они не заменяют переписку: не придумывай, о чём спорили или чем занимались "
        "лагеря и пары, если этого нет в материале чата. Не называй проценты, числа и слова «соцграф», «модулярность», «метрика»."
    )
    return "\n".join(lines)


async def _faction_titles(structure, chat_id: int) -> dict[int, str]:
    from features.social_graph.overview import faction_names

    try:
        return await asyncio.wait_for(faction_names(structure), timeout=FACTION_NAME_TIMEOUT_SECONDS)
    except Exception:
        logging.warning("[radio][social] faction naming failed chat=%s; using fallback names", chat_id)
        return await faction_names(structure, use_ai=False)


async def _relationships(chat_id: int) -> list:
    from features.social_graph.relationships import get_relationships

    try:
        return await get_relationships(chat_id)
    except Exception:
        logging.exception("[radio][social] relationships failed chat=%s", chat_id)
        return []


async def build_radio_structure_context(
    chat_id: int | str,
    period_hours: int,
    *,
    now: datetime | None = None,
) -> str:
    """Factions, roles and relationship labels relevant to a Radio episode window."""
    from features.social_graph import service as social_service
    from features.social_graph.overview import load_structure

    chat_id = int(chat_id)
    if not social_service.is_social_graph_enabled(chat_id):
        return ""

    since = _utc(now or datetime.now(timezone.utc)) - timedelta(hours=max(1, int(period_hours)))
    interactions, window_names = await asyncio.to_thread(social_service._repository().load_graph, chat_id, since)
    window_edges = aggregate_edges(interactions)
    if not window_edges:
        return ""

    structure = await load_structure(chat_id)
    titles, relationships = await asyncio.gather(_faction_titles(structure, chat_id), _relationships(chat_id))
    return format_radio_structure(structure, titles, window_edges, window_names, relationships)
