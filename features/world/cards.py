"""State cards for World of Upupa with privacy rules applied.

* A state always sees everything about itself, and only its own views may
  trigger a fresh identity analysis.
* Other states see the cached identity only when ``share_identity`` is on, and
  activity data (top citizen, message counts, social graph) only when
  ``share_activity`` is on.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from core.history_store import get_history_repository
from core.paths import USER_MESSAGES_LOG_PATH
from features.world.activity import get_top_active_citizen
from features.world.blocs import bloc_of
from features.world.identity import ensure_state_identity
from features.world.presentation import format_world_profile
from features.world.service import WorldService
from features.world.symbols import WorldSymbolStore, symbol_kind_label


@dataclass(frozen=True)
class StateCharacteristics:
    messages_7d: int
    active_citizens_7d: int
    social_edges_30d: int
    cohesion: int | None


@dataclass(frozen=True)
class StateCard:
    text: str
    world_id: int
    own: bool
    flag_file_id: str | None = None


def symbol_store(service: WorldService) -> WorldSymbolStore | None:
    path = getattr(getattr(service, "ledger", None), "path", None)
    return WorldSymbolStore(path) if path is not None else None


def _weighted_cohesion(edges) -> int | None:
    total_weight = sum(edge.total_weight for edge in edges)
    if total_weight <= 0:
        return None
    reciprocity = sum(edge.reciprocity * edge.total_weight for edge in edges) / total_weight
    return max(0, min(100, round(reciprocity * 100)))


async def _activity_metrics(chat_id: int) -> tuple[int, int]:
    history = get_history_repository(USER_MESSAGES_LOG_PATH)
    if history is None:
        return 0, 0
    since = datetime.now() - timedelta(days=7)
    try:
        messages, participants = await asyncio.gather(
            asyncio.to_thread(history.count, str(chat_id), start=since, nonempty=True),
            asyncio.to_thread(history.participants, str(chat_id), start=since, nonempty=True),
        )
        return int(messages), len(participants)
    except Exception:
        return 0, 0


async def get_state_characteristics(chat_id: int) -> StateCharacteristics:
    from features.social_graph.analysis import aggregate_edges
    from features.social_graph.service import get_graph_data, is_social_graph_enabled

    messages_7d, citizens_7d = await _activity_metrics(chat_id)
    edges = ()
    if is_social_graph_enabled(chat_id):
        try:
            graph = await get_graph_data(chat_id, period_days=30)
            edges = aggregate_edges(graph.interactions)
        except Exception:
            edges = ()
    return StateCharacteristics(
        messages_7d=messages_7d,
        active_citizens_7d=citizens_7d,
        social_edges_30d=len(edges),
        cohesion=_weighted_cohesion(edges),
    )


def _characteristics_block(metrics: StateCharacteristics) -> str:
    cohesion = f"{metrics.cohesion}/100" if metrics.cohesion is not None else "нет данных"
    return "\n".join(
        [
            "",
            "Характеристики государства:",
            f"📨 Активность за 7 дней: {metrics.messages_7d} сообщений",
            f"🧑‍🤝‍🧑 Активных граждан за 7 дней: {metrics.active_citizens_7d}",
            f"🕸 Социальных связей за 30 дней: {metrics.social_edges_30d}",
            f"🤜🤛 Взаимность связей: {cohesion}",
        ]
    )


async def build_state_card(
    service: WorldService,
    world_id: int,
    *,
    bot=None,
    viewer_world_id: int | None = None,
    analyze: bool = True,
) -> StateCard | None:
    profile = await service.get_profile_by_world_id(world_id)
    if profile is None:
        return None
    state = profile.state
    own = viewer_world_id is not None and int(viewer_world_id) == int(world_id)
    details = await service.get_details(world_id)
    share_identity = own or bool(details and details.share_identity)
    share_activity = own or bool(details and details.share_activity)

    identity_task = (
        ensure_state_identity(service, state, allow_analysis=own and analyze)
        if share_identity
        else asyncio.sleep(0, result=None)
    )
    top_task = get_top_active_citizen(state.chat_id) if share_activity else asyncio.sleep(0, result=None)
    identity, top_active, alliance_names, authority, bloc = await asyncio.gather(
        identity_task,
        top_task,
        service.get_alliance_names(world_id),
        service.get_authority(world_id),
        bloc_of(service, world_id),
    )

    population = None
    if bot is not None:
        try:
            population = await bot.get_chat_member_count(state.chat_id)
        except Exception as exc:
            logging.warning("World population lookup failed chat_id=%s: %s", state.chat_id, exc)

    store = symbol_store(service)
    symbols = await asyncio.to_thread(store.get_symbols, world_id) if store else {}
    symbol_line = None
    if symbols:
        kinds = [symbol_kind_label(kind) for kind in ("flag", "emblem") if kind in symbols]
        symbol_line = f"🏴 Символика: {', '.join(kinds)}"

    tourism_line = None
    if service.ledger is not None:
        ratings = await asyncio.to_thread(service.ledger.tourism_ratings)
        if world_id in ratings:
            average, votes = ratings[world_id]
            tourism_line = f"🧳 Туризм: ★{average:.1f} ({votes} оценок за 30 дней)"

    shown_details = identity.details if identity is not None else None
    most_active = None
    if top_active:
        name, count = top_active
        most_active = f"{name} ({count} сообщ.)"

    bloc_line = None
    if bloc is not None:
        bloc_line = f"🧱 Блок: «{bloc.name}» ({len(bloc.members)} гос.)"

    text = format_world_profile(
        profile,
        population,
        details=shown_details,
        authority=authority,
        detailed_authority=own,
        most_active=most_active,
        alliance_names=alliance_names,
        identity_rationale=(identity.rationale if identity is not None else None),
        bloc_line=bloc_line,
        symbol_line=symbol_line,
        tourism_line=tourism_line,
    )
    if details is not None and details.ambassador_name and shown_details is None:
        text = text.replace("\n\nДипломатические отношения:", f"\n🎩 Посол: {details.ambassador_name}\n\nДипломатические отношения:", 1)
    if not share_identity:
        text = text.replace(
            "\n\nДипломатические отношения:",
            "\n🔒 Характеристику государство держит при себе.\n\nДипломатические отношения:",
            1,
        )
    if share_activity:
        metrics = await get_state_characteristics(state.chat_id)
        text += _characteristics_block(metrics)
    flag = symbols.get("flag") or symbols.get("emblem")
    return StateCard(
        text=text,
        world_id=world_id,
        own=own,
        flag_file_id=flag.telegram_file_id if flag else None,
    )
