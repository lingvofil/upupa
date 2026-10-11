"""Human-readable views over the social graph: roles, factions and the dossier.

Everything here composes deterministic facts (graph, relationships, Chronicle)
into short HTML cards.  AI is used for exactly one thing — naming factions —
and is cached, optional and replaceable by a deterministic fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import time
from typing import Mapping

from features.chronicle.models import ChronicleEvent
from features.chronicle.runtime import count_events, list_events
from features.social_graph.ai import name_factions
from features.social_graph.dynamics import _plural
from features.social_graph.analysis import GraphEdge, PersonalConnection, aggregate_edges, build_personal_summary
from features.social_graph.relationships import RelationshipView, get_relationships
from features.social_graph.rendering import UNAFFILIATED_NODE_COLOR, faction_color
from features.social_graph.service import get_graph_data
from features.social_graph.structure import (
    ROLE_DEFINITIONS,
    FactionMap,
    ParticipantRole,
    assign_roles,
    detect_factions,
    fallback_faction_name,
)


FACTION_NAME_TTL_SECONDS = 6 * 3600
DOSSIER_RELATIONSHIPS = 3
ANTAGONIST_MIN_TENSION = 25
_faction_name_cache: dict[tuple[int, tuple[tuple[int, ...], ...]], tuple[float, dict[int, str]]] = {}


@dataclass(frozen=True, slots=True)
class ChatStructure:
    chat_id: int
    period_days: int
    names: dict[int, str]
    interactions: tuple[tuple[int, int, str, float], ...]
    edges: tuple[GraphEdge, ...]
    roles: dict[int, ParticipantRole]
    factions: FactionMap


async def load_structure(chat_id: int, *, period_days: int | None = None) -> ChatStructure:
    data = await (get_graph_data(chat_id, period_days=period_days) if period_days else get_graph_data(chat_id))
    edges = aggregate_edges(data.interactions)
    return ChatStructure(
        chat_id=int(chat_id),
        period_days=data.period_days,
        names=dict(data.names),
        interactions=tuple(data.interactions),
        edges=edges,
        roles=assign_roles(data.interactions, edges),
        factions=detect_factions(edges),
    )


def _faction_key(structure: ChatStructure) -> tuple[int, tuple[tuple[int, ...], ...]]:
    return structure.chat_id, tuple(tuple(sorted(f.members)) for f in structure.factions.factions)


def cached_faction_names(structure: ChatStructure) -> dict[int, str]:
    entry = _faction_name_cache.get(_faction_key(structure))
    if entry is None or time.monotonic() - entry[0] > FACTION_NAME_TTL_SECONDS:
        return {}
    return dict(entry[1])


async def faction_names(structure: ChatStructure, *, use_ai: bool = True) -> dict[int, str]:
    """AI names for the current partition, cached until the partition changes."""
    factions = structure.factions.factions
    names = cached_faction_names(structure)
    if use_ai and factions and len(names) < len(factions):
        generated = await name_factions(factions, structure.names, structure.roles, str(structure.chat_id))
        if generated:
            names.update(generated)
            _faction_name_cache[_faction_key(structure)] = (time.monotonic(), dict(names))
    for faction in factions:
        names.setdefault(faction.index, fallback_faction_name(faction, structure.names))
    return names


def faction_colors(structure: ChatStructure) -> dict[int, tuple[int, int, int]]:
    colors = {user_id: faction_color(index) for user_id, index in structure.factions.membership.items()}
    for user_id in structure.factions.unaffiliated:
        colors[user_id] = UNAFFILIATED_NODE_COLOR
    return colors


def faction_legend(structure: ChatStructure, names: Mapping[int, str]) -> list[tuple[tuple[int, int, int], str]]:
    legend = [(faction_color(f.index), names.get(f.index, f"Фракция {f.index}")) for f in structure.factions.factions]
    if structure.factions.unaffiliated:
        legend.append((UNAFFILIATED_NODE_COLOR, "Вне фракций"))
    return legend


def _name(names: Mapping[int, str], user_id: int) -> str:
    return escape(names.get(user_id, "Участник"))


def format_roles_html(structure: ChatStructure, *, limit: int = 15) -> str:
    if not structure.roles:
        return ""
    order = list(ROLE_DEFINITIONS)
    ranked = sorted(
        structure.roles.values(),
        key=lambda role: (order.index(role.role.key), -role.stats.strength, role.user_id),
    )
    lines = [f"🎭 <b>Роли в чате</b> · за {structure.period_days} дней", ""]
    for role in ranked[:limit]:
        lines.append(f"{role.role.emoji} <b>{_name(structure.names, role.user_id)}</b> — {escape(role.role.title.lower())}: {escape(role.reason)}")
    hidden = len(ranked) - limit
    if hidden > 0:
        lines.append(f"…и ещё {hidden} человек без особых примет.")
    lines.extend(("", "<i>Роли считаются по реплаям, упоминаниям и реакциям, без чтения переписки.</i>"))
    return "\n".join(lines)


def format_factions_html(structure: ChatStructure, names: Mapping[int, str]) -> str:
    factions = structure.factions.factions
    if not factions:
        return ""
    if len(factions) == 1 and not structure.factions.unaffiliated:
        intro = "Чат слишком сплочённый, чтобы делиться на лагеря: все в одной большой фракции."
    elif len(factions) == 1:
        intro = "Есть одно ядро и несколько участников, которые держатся особняком."
    else:
        count = len(factions)
        intro = (
            f"Чат распался на {count} {_plural(count, 'лагерь', 'лагеря', 'лагерей')} — "
            "внутри них общаются заметно плотнее, чем между ними."
        )
    lines = [f"🏴 <b>Фракции чата</b> · за {structure.period_days} дней", escape(intro), ""]
    for faction in factions:
        members = ", ".join(_name(structure.names, user_id) for user_id in faction.members[:10])
        if len(faction.members) > 10:
            members += f" и ещё {len(faction.members) - 10}"
        lines.append(f"<b>{escape(names.get(faction.index, f'Фракция {faction.index}'))}</b>")
        lines.append(f"Лидер по связям: {_name(structure.names, faction.leader_id)} · сплочённость {round(faction.cohesion * 100)}%")
        lines.append(f"Состав: {members}")
        lines.append("")
    if structure.factions.liaison_id is not None:
        lines.append(f"🤝 Главный связной между лагерями: <b>{_name(structure.names, structure.factions.liaison_id)}</b>")
    if structure.factions.unaffiliated:
        loners = ", ".join(_name(structure.names, user_id) for user_id in structure.factions.unaffiliated[:8])
        lines.append(f"🦉 Вне фракций: {loners}")
    return "\n".join(lines).strip()


# --- dossier ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Dossier:
    user_id: int
    name: str
    period_days: int
    role: ParticipantRole | None
    faction_name: str | None
    connections: int
    relationships: tuple[RelationshipView, ...]
    antagonist: RelationshipView | None
    asymmetry: PersonalConnection | None
    graph_names: dict[int, str]
    chronicle_count: int
    latest_event: ChronicleEvent | None
    faction_index: int | None = None
    antagonist_name: str | None = None


def _other(view: RelationshipView, user_id: int) -> tuple[int, str]:
    if view.user_a_id == int(user_id):
        return view.user_b_id, view.user_b_name
    return view.user_a_id, view.user_a_name


async def build_dossier(chat_id: int, user_id: int, fallback_name: str) -> Dossier:
    structure = await load_structure(chat_id)
    relationships = await get_relationships(chat_id, user_id=user_id)
    summary = build_personal_summary(int(user_id), structure.edges)
    try:
        chronicle_count = await count_events(chat_id, user_id=user_id)
        latest = await list_events(chat_id, user_id=user_id, limit=1) if chronicle_count else []
    except Exception:
        chronicle_count, latest = 0, []

    faction_name = None
    index = structure.factions.membership.get(int(user_id))
    if index is not None:
        names = cached_faction_names(structure)
        faction = next(f for f in structure.factions.factions if f.index == index)
        faction_name = names.get(index) or fallback_faction_name(faction, structure.names)

    tense = [view for view in relationships if view.tension >= ANTAGONIST_MIN_TENSION]
    antagonist = max(tense, key=lambda view: (view.tension, view.level)) if tense else None
    top = tuple(view for view in relationships if view is not antagonist)[:DOSSIER_RELATIONSHIPS]

    name = structure.names.get(int(user_id))
    if not name:
        for view in relationships:
            if view.user_a_id == int(user_id):
                name = view.user_a_name
                break
            if view.user_b_id == int(user_id):
                name = view.user_b_name
                break
    name = name or fallback_name or "Участник"
    return Dossier(
        user_id=int(user_id),
        name=name,
        period_days=structure.period_days,
        role=structure.roles.get(int(user_id)),
        faction_name=faction_name,
        connections=summary.distinct_connections,
        relationships=top,
        antagonist=antagonist,
        asymmetry=summary.strongest_asymmetry,
        graph_names=structure.names,
        chronicle_count=int(chronicle_count),
        latest_event=latest[0] if latest else None,
        faction_index=index,
        antagonist_name=_other(antagonist, user_id)[1] if antagonist is not None else None,
    )


def format_dossier_html(dossier: Dossier, event_link: str | None = None) -> str:
    lines = [f"🗂 <b>Досье: {escape(dossier.name)}</b>", ""]
    if dossier.role is not None:
        lines.append(f"Роль: {dossier.role.role.emoji} <b>{escape(dossier.role.role.title)}</b> — {escape(dossier.role.role.blurb)}.")
    else:
        lines.append(f"Роль: не определена — за {dossier.period_days} дней почти нет взаимодействий.")
    if dossier.faction_name:
        lines.append(f"Фракция: <b>{escape(dossier.faction_name)}</b>")
    if dossier.connections:
        lines.append(f"Связей за {dossier.period_days} дней: <b>{dossier.connections}</b>")

    if dossier.relationships:
        lines.extend(("", "<b>Главные отношения</b>"))
        for view in dossier.relationships:
            _other_id, other_name = _other(view, dossier.user_id)
            lines.append(f"• {escape(other_name)} — ур. {view.level}/100, «{escape(view.archetype)}», {escape(view.trend)}")
    if dossier.antagonist is not None:
        _other_id, other_name = _other(dossier.antagonist, dossier.user_id)
        lines.append(f"⚔️ Главный оппонент: <b>{escape(other_name)}</b> — напряжение {dossier.antagonist.tension}/100")

    if dossier.asymmetry is not None:
        other = escape(dossier.graph_names.get(dossier.asymmetry.user_id, "Участник"))
        if dossier.asymmetry.outgoing > dossier.asymmetry.incoming:
            lines.append(f"🫠 Перекос: сам заметно чаще обращается к {other}, чем наоборот.")
        else:
            lines.append(f"🧲 Перекос: {other} обращается к нему заметно чаще, чем он в ответ.")

    lines.append("")
    if dossier.chronicle_count and dossier.latest_event is not None:
        title = escape(dossier.latest_event.title)
        if event_link:
            title = f'<a href="{escape(event_link, quote=True)}">{title}</a>'
        lines.append(f"📜 В Летописи: <b>{dossier.chronicle_count}</b>, последнее — «{title}»")
    else:
        lines.append("📜 В Летописи пока не засветился.")
    return "\n".join(lines)


def dossier_card_data(dossier: Dossier, avatar: bytes | None = None):
    from features.social_graph.cards import DossierCardData, DossierRelation

    relations = tuple(
        DossierRelation(name=_other(view, dossier.user_id)[1], level=view.level, archetype=view.archetype)
        for view in dossier.relationships
    )
    antagonist = None
    if dossier.antagonist is not None:
        antagonist = DossierRelation(
            name=dossier.antagonist_name or "Участник",
            level=dossier.antagonist.level,
            archetype=dossier.antagonist.archetype,
        )
    return DossierCardData(
        user_id=dossier.user_id,
        name=dossier.name,
        role_title=dossier.role.role.title if dossier.role else None,
        role_blurb=dossier.role.role.blurb if dossier.role else None,
        faction_name=dossier.faction_name,
        faction_color=faction_color(dossier.faction_index) if dossier.faction_index else None,
        connections=dossier.connections,
        period_days=dossier.period_days,
        relations=relations,
        antagonist=antagonist,
        antagonist_tension=dossier.antagonist.tension if dossier.antagonist is not None else 0,
        chronicle_count=dossier.chronicle_count,
        latest_event=dossier.latest_event.title if dossier.latest_event is not None else None,
        avatar=avatar,
    )


def format_dossier_caption_html(dossier: Dossier, event_link: str | None = None) -> str:
    """Short caption under the dossier card: what the picture cannot hold (links, perekos)."""
    lines = [f"🗂 <b>Досье: {escape(dossier.name)}</b>"]
    if dossier.role is not None:
        lines.append(f"{dossier.role.role.emoji} {escape(dossier.role.reason)}")
    if dossier.asymmetry is not None:
        other = escape(dossier.graph_names.get(dossier.asymmetry.user_id, "Участник"))
        if dossier.asymmetry.outgoing > dossier.asymmetry.incoming:
            lines.append(f"🫠 Сам заметно чаще обращается к {other}, чем наоборот.")
        else:
            lines.append(f"🧲 {other} обращается к нему заметно чаще, чем он в ответ.")
    if dossier.latest_event is not None and event_link:
        lines.append(f'📜 <a href="{escape(event_link, quote=True)}">{escape(dossier.latest_event.title)}</a>')
    return "\n".join(lines)

