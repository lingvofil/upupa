"""Human-readable views over the social graph: roles, factions and the dossier.

Everything here composes deterministic facts (graph, relationships, Chronicle)
into short HTML cards.  AI is used for exactly one thing — naming factions —
and is cached, optional and replaceable by a deterministic fallback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import date
from html import escape
import time
from typing import Mapping

from core.time_utils import app_now_naive
from features.chronicle.models import ChronicleEvent
from features.chronicle.runtime import count_events, list_events
from features.social_graph.ai import name_factions
from features.social_graph.dossier_facts import (
    UPUPA_PERIOD_DAYS,
    PersonalFacts,
    activity_phrase,
    birthday_countdown,
    birthday_date,
    chronotype_emoji,
    crocodile_phrase,
    dnd_phrase,
    dossier_nickname,
    duration_phrase,
    load_personal_facts,
    messages_phrase,
    number,
    reactions_card_phrase,
    reactions_emoji_phrase,
    rhythm_phrase,
    silence_days,
    upupa_phrase,
    verbosity_phrase,
    words_phrase,
)
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
NICKNAME_MIN_FACTS = 2
CAPTION_LINE_CHARS = 60
CAPTION_QUOTE_CHARS = 40
DETAILS_QUOTE_CHARS = 60
DETAILS_HERO_CHARS = 50
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
    facts: PersonalFacts = PersonalFacts()
    nickname: str | None = None


def _other(view: RelationshipView, user_id: int) -> tuple[int, str]:
    if view.user_a_id == int(user_id):
        return view.user_b_id, view.user_b_name
    return view.user_a_id, view.user_a_name


async def build_dossier(chat_id: int, user_id: int, fallback_name: str, *, use_ai: bool = True) -> Dossier:
    structure, relationships, facts = await asyncio.gather(
        load_structure(chat_id),
        get_relationships(chat_id, user_id=user_id),
        load_personal_facts(chat_id, user_id),
    )
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
    dossier = Dossier(
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
        facts=facts,
    )
    hints = nickname_facts(dossier)
    if use_ai and len(hints) >= NICKNAME_MIN_FACTS:
        dossier = replace(dossier, nickname=await dossier_nickname(chat_id, user_id, name, hints))
    return dossier


def nickname_facts(dossier: Dossier) -> list[str]:
    """Plain-language facts the nickname AI may lean on — nothing it could not see in the dossier."""
    facts = dossier.facts
    lines = []
    if dossier.role is not None:
        lines.append(f"роль в чате: {dossier.role.role.title.lower()} — {dossier.role.role.blurb}")
    if dossier.faction_name:
        lines.append(f"фракция: {dossier.faction_name}")
    if dossier.relationships:
        view = dossier.relationships[0]
        lines.append(f"ближе всех с {_other(view, dossier.user_id)[1]}, их пара — «{view.archetype}»")
    if dossier.antagonist_name:
        lines.append(f"главный оппонент: {dossier.antagonist_name}")
    if facts.activity is not None and facts.activity.total:
        rank = f", ранг «{facts.activity.rank}»" if facts.activity.rank else ""
        lines.append(f"в чате {messages_phrase(facts.activity.total)}{rank}")
    if facts.rhythm is not None:
        lines.append(f"ритм: {rhythm_phrase(facts.rhythm)}")
    if facts.reactions is not None and facts.reactions.given:
        lines.append(f"чаще всего ставит реакцию {facts.reactions.given[0][0]}")
    if facts.reactions is not None and facts.reactions.received:
        lines.append(f"на сообщения чаще всего ставят {facts.reactions.received[0][0]}")
    if facts.words:
        lines.append("любимые слова: " + ", ".join(word for word, _count in facts.words))
    if facts.average_words is not None:
        lines.append(verbosity_phrase(facts.average_words) + " на сообщение")
    if facts.crocodile is not None and facts.crocodile.draws:
        lines.append(f"в «Крокодиле» угадали рисунков: {facts.crocodile.draws}")
    if facts.dnd is not None:
        hero = f", герой — {facts.dnd.hero}" if facts.dnd.hero else ""
        lines.append(f"в ДнД приключений: {facts.dnd.adventures}{hero}" + (", герой погиб" if facts.dnd.dead else ""))
    if facts.upupa is not None and facts.upupa.favorites:
        lines.append(f"чаще всего просит бота: «{facts.upupa.favorites[0][0]}»")
    return lines


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _title_html(dossier: Dossier) -> str:
    title = f"🗂 <b>Досье: {escape(dossier.name)}</b>"
    if dossier.nickname:
        title += f" — «{escape(dossier.nickname)}»"
    return title


def _asymmetry_html(dossier: Dossier) -> str | None:
    if dossier.asymmetry is None:
        return None
    other = escape(dossier.graph_names.get(dossier.asymmetry.user_id, "Участник"))
    if dossier.asymmetry.outgoing > dossier.asymmetry.incoming:
        return f"🫠 Сам заметно чаще обращается к {other}, чем наоборот."
    return f"🧲 {other} обращается к нему заметно чаще, чем он в ответ."


def _hit_html(dossier: Dossier, hit_link: str | None, *, limit: int) -> str | None:
    reactions = dossier.facts.reactions
    if reactions is None or reactions.best_message_id is None or not reactions.best_message_reactions:
        return None
    quote = escape(_short(reactions.best_message_text, limit)) or "без текста"
    if hit_link:
        quote = f'<a href="{escape(hit_link, quote=True)}">{quote}</a>'
    count = reactions.best_message_reactions
    return f"💬 Хит: «{quote}» — {count} {_plural(count, 'реакция', 'реакции', 'реакций')}"


def _event_html(dossier: Dossier, event_link: str | None, *, limit: int = 80) -> str:
    title = escape(_short(dossier.latest_event.title, limit))
    if event_link:
        title = f'<a href="{escape(event_link, quote=True)}">{title}</a>'
    return title


def _section(lines: list[str], title: str, block: list[str]) -> None:
    if block:
        lines.extend(("", f"<b>{title}</b>", *block))


def _quoted(items, limit: int = 3) -> str:
    return ", ".join(f"«{escape(label)}»" for label, _count in list(items)[:limit])


def format_dossier_html(
    dossier: Dossier,
    event_link: str | None = None,
    hit_link: str | None = None,
    *,
    today: date | None = None,
) -> str:
    """The whole dossier as text — the «Подробнее» page and the fallback when the card fails.

    One short line per fact, no counters next to every item: the page has to be
    read at a glance, the card already shows the numbers that matter.
    """
    today = today or app_now_naive().date()
    facts = dossier.facts
    lines = [_title_html(dossier)]
    if dossier.role is not None:
        lines.append(f"{dossier.role.role.emoji} <b>{escape(dossier.role.role.title)}</b>: {escape(dossier.role.reason)}")
    else:
        lines.append("Роль пока не определена: взаимодействий почти нет.")
    if dossier.faction_name:
        lines.append(f"🏴 Фракция: <b>{escape(dossier.faction_name)}</b>")

    block = []
    for view in dossier.relationships:
        _other_id, other_name = _other(view, dossier.user_id)
        block.append(f"• {escape(other_name)} — {view.level}/100, «{escape(view.archetype)}»")
    if dossier.antagonist is not None:
        _other_id, other_name = _other(dossier.antagonist, dossier.user_id)
        block.append(f"⚔️ Оппонент: <b>{escape(other_name)}</b>, напряжение {dossier.antagonist.tension}/100")
    if asymmetry := _asymmetry_html(dossier):
        block.append(asymmetry)
    _section(lines, "Отношения", block)

    block = []
    activity = facts.activity
    if activity is not None and (activity.rank or activity.total):
        parts = [escape(activity.rank)] if activity.rank else []
        if activity.total:
            parts.append(f"{messages_phrase(activity.total)}, {number(activity.weekly)} за неделю")
        block.append("🏅 " + " · ".join(parts))
    if activity is not None and (quiet := silence_days(activity.last_seen, today)):
        block.append(f"💤 Молчит {quiet} {_plural(quiet, 'день', 'дня', 'дней')}")
    if facts.rhythm is not None:
        phrase = rhythm_phrase(facts.rhythm)
        block.append(f"{chronotype_emoji(facts.rhythm)} {phrase[0].upper()}{phrase[1:]}")
    if facts.birthday is not None:
        block.append(f"🎂 ДР {birthday_date(facts.birthday)}, {birthday_countdown(facts.birthday)}")
    _section(lines, "Активность", block)

    block = []
    reactions = facts.reactions
    if reactions is not None and (reactions.given or reactions.received):
        parts = []
        if reactions.given:
            parts.append(f"ставит {reactions_emoji_phrase(reactions.given)}")
        if reactions.received:
            parts.append(f"получает {reactions_emoji_phrase(reactions.received)}")
        text = " · ".join(parts)
        block.append(f"🎯 {text[0].upper()}{text[1:]}")
    if hit := _hit_html(dossier, hit_link, limit=DETAILS_QUOTE_CHARS):
        block.append(hit)
    if facts.average_words is not None:
        phrase = verbosity_phrase(facts.average_words)
        block.append(f"✍️ {phrase[0].upper()}{phrase[1:]} на сообщение")
    if facts.words:
        block.append(f"🗣 Словечки: {_quoted(facts.words)}")
    _section(lines, "Реакции и слог", block)

    block = []
    crocodile = facts.crocodile
    if crocodile is not None:
        parts = []
        if crocodile.draws:
            parts.append(f"угадали {crocodile.draws} {_plural(crocodile.draws, 'рисунок', 'рисунка', 'рисунков')}")
            if crocodile.average_seconds is not None:
                parts[-1] += f" за {duration_phrase(crocodile.average_seconds)} в среднем"
        if crocodile.likes:
            parts.append(f"{crocodile.likes} ❤️")
        block.append("🎨 Кракадил: " + ", ".join(parts))
    dnd = facts.dnd
    if dnd is not None:
        line = f"🧙 ДнД: {dnd.adventures} {_plural(dnd.adventures, 'приключение', 'приключения', 'приключений')}"
        if dnd.hero:
            line += f", герой «{escape(_short(dnd.hero, DETAILS_HERO_CHARS))}»"
        if dnd.dead:
            line += " ☠️"
        block.append(line)
    _section(lines, "Игры", block)

    tail = []
    if facts.upupa is not None:
        requests = facts.upupa.requests
        tail.append(
            f"🤖 Просил Упупу {requests} {_plural(requests, 'раз', 'раза', 'раз')} за {UPUPA_PERIOD_DAYS} дней, "
            f"чаще всего {_quoted(facts.upupa.favorites)}"
        )
    if dossier.latest_event is not None:
        tail.append(f"📜 Последнее в Летописи: {_event_html(dossier, event_link)}")
    else:
        tail.append("📜 В Летописи пока не засветился.")
    lines.extend(("", *tail))
    return "\n".join(lines)


def dossier_card_data(dossier: Dossier, avatar: bytes | None = None, *, today: date | None = None):
    from features.social_graph.cards import DossierCardData, DossierRelation

    today = today or app_now_naive().date()
    facts = dossier.facts
    closest = None
    if dossier.relationships:
        view = dossier.relationships[0]
        closest = DossierRelation(name=_other(view, dossier.user_id)[1], level=view.level, archetype=view.archetype)
    antagonist = None
    if dossier.antagonist is not None:
        antagonist = DossierRelation(
            name=dossier.antagonist_name or "Участник",
            level=dossier.antagonist.level,
            archetype=dossier.antagonist.archetype,
        )

    side_lines = []
    activity = facts.activity
    if activity is not None and activity.rank:
        side_lines.append(f"ранг: {activity.rank}")
    if facts.birthday is not None:
        side_lines.append(f"ДР {birthday_date(facts.birthday)}, {birthday_countdown(facts.birthday)}")
    if facts.average_words is not None:
        side_lines.append(verbosity_phrase(facts.average_words))

    rows = []
    if activity is not None and (phrase := activity_phrase(activity, today)):
        rows.append(("Активность", phrase))
    if facts.reactions is not None and (phrase := reactions_card_phrase(facts.reactions)):
        rows.append(("Реакции", phrase))
    if phrase := words_phrase(facts.words):
        rows.append(("Словечки", phrase))
    if facts.crocodile is not None and (phrase := crocodile_phrase(facts.crocodile)):
        rows.append(("Кракадил", phrase))
    if facts.dnd is not None:
        rows.append(("ДнД", dnd_phrase(facts.dnd)))
    if facts.upupa is not None:
        rows.append(("Упупа", upupa_phrase(facts.upupa)))
    if dossier.latest_event is not None:
        rows.append(("Летопись", f"«{dossier.latest_event.title}»"))

    return DossierCardData(
        user_id=dossier.user_id,
        name=dossier.name,
        role_title=dossier.role.role.title if dossier.role else None,
        role_blurb=dossier.role.role.blurb if dossier.role else None,
        faction_name=dossier.faction_name,
        faction_color=faction_color(dossier.faction_index) if dossier.faction_index else None,
        nickname=dossier.nickname,
        closest=closest,
        antagonist=antagonist,
        antagonist_tension=dossier.antagonist.tension if dossier.antagonist is not None else 0,
        side_lines=tuple(side_lines),
        rhythm_hours=facts.rhythm.hours if facts.rhythm is not None else (),
        rhythm_label=rhythm_phrase(facts.rhythm, compact=True) if facts.rhythm is not None else None,
        rows=tuple(rows),
        avatar=avatar,
    )


def format_dossier_caption_html(dossier: Dossier, event_link: str | None = None, hit_link: str | None = None) -> str:
    """Caption under the card: at most four short lines the picture cannot hold.

    The role's concrete reason and two clickable links; everything else is on
    the card or behind «Подробнее», so the caption never turns into a wall of text.
    """
    lines = [_title_html(dossier)]
    if dossier.role is not None:
        lines.append(f"{dossier.role.role.emoji} {escape(_short(dossier.role.reason, CAPTION_LINE_CHARS))}")
    if hit := _hit_html(dossier, hit_link, limit=CAPTION_QUOTE_CHARS):
        lines.append(hit)
    if dossier.latest_event is not None and event_link:
        lines.append(f"📜 {_event_html(dossier, event_link, limit=CAPTION_LINE_CHARS)}")
    return "\n".join(lines)
