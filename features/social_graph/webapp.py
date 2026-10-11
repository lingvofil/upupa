"""Data and links for the interactive social-graph Mini App.

The Mini App reuses the bot's single registered Telegram Mini App: a direct
link ``t.me/<bot>/<app>?startapp=sg_<room>`` opens ``index.html``, which
redirects ``sg_*`` start params to the social view.  ``start_param`` is part
of the signed ``initData``, so the server trusts the room from the signature,
and additionally checks that the viewer is a member of that chat.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import time
from typing import Any

from features.chronicle.runtime import list_events
from features.social_graph.interaction_analysis import build_edge_interaction_profiles
from features.social_graph.overview import cached_faction_names, load_structure
from features.social_graph.relationships import get_relationships
from features.social_graph.rendering import UNAFFILIATED_NODE_COLOR, faction_color
from features.social_graph.service import _without_username_tag
from features.social_graph.structure import fallback_faction_name


START_PARAM_PREFIX = "sg_"
ALLOWED_PERIODS = (7, 30, 90)
MAX_NODES = 60
MAX_RELATIONSHIPS = 80
MAX_EVENTS = 60
_ROOM_RE = re.compile(r"^m(\d{5,20})$")


def room_for_chat(chat_id: int) -> str:
    """Telegram start params allow only [A-Za-z0-9_-]: «-100…» becomes «m100…»."""
    chat_id = int(chat_id)
    if chat_id >= 0:
        raise ValueError("social Mini App is only for group chats")
    return f"m{abs(chat_id)}"


def chat_from_start_param(start_param: str | None) -> int | None:
    value = str(start_param or "")
    if not value.startswith(START_PARAM_PREFIX):
        return None
    match = _ROOM_RE.fullmatch(value[len(START_PARAM_PREFIX):])
    if not match:
        return None
    return -int(match.group(1))


def mini_app_link(chat_id: int) -> str | None:
    """Direct Mini App link that works from group chats (web_app buttons do not)."""
    try:
        from games.crocodile import BOT_USERNAME, WEB_APP_SHORT_NAME
    except Exception:
        return None
    try:
        room = room_for_chat(chat_id)
    except ValueError:
        return None
    return f"https://t.me/{BOT_USERNAME}/{WEB_APP_SHORT_NAME}?startapp={START_PARAM_PREFIX}{room}"


def _hex(color: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def _message_link(chat_id: int, message_id: int | None) -> str | None:
    raw = str(int(chat_id))
    if message_id is None or not raw.startswith("-100"):
        return None
    return f"https://t.me/c/{raw[4:]}/{int(message_id)}"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _event_payload(chat_id: int, event) -> dict[str, Any]:
    message_id = event.anchor_message_id or (event.source_message_ids[0] if event.source_message_ids else None)
    return {
        "id": str(event.id),
        "title": str(event.title),
        "summary": str(event.summary),
        "category": str(event.category),
        "date": _iso(event.event_started_at),
        "importance": round(float(event.importance_score), 2),
        "participants": [int(item) for item in event.participant_ids],
        "link": _message_link(chat_id, message_id),
    }


async def build_social_payload(chat_id: int, *, period_days: int = 30, viewer_id: int | None = None) -> dict[str, Any]:
    period_days = period_days if period_days in ALLOWED_PERIODS else 30
    structure = await load_structure(chat_id, period_days=period_days)

    strength: dict[int, float] = {}
    for edge in structure.edges:
        strength[edge.user_a] = strength.get(edge.user_a, 0.0) + edge.total_weight
        strength[edge.user_b] = strength.get(edge.user_b, 0.0) + edge.total_weight
    visible = set(sorted(strength, key=lambda uid: (-strength[uid], uid))[:MAX_NODES])
    edges = [edge for edge in structure.edges if edge.user_a in visible and edge.user_b in visible]
    profiles = build_edge_interaction_profiles(structure.interactions, edges)

    names = cached_faction_names(structure)
    factions = []
    for faction in structure.factions.factions:
        factions.append({
            "index": faction.index,
            "name": names.get(faction.index) or fallback_faction_name(faction, structure.names),
            "color": _hex(faction_color(faction.index)),
            "members": [int(uid) for uid in faction.members],
            "leader": int(faction.leader_id),
            "cohesion": round(faction.cohesion, 3),
        })

    nodes = []
    for user_id in sorted(visible, key=lambda uid: (-strength[uid], uid)):
        role = structure.roles.get(user_id)
        faction_index = structure.factions.membership.get(user_id)
        nodes.append({
            "id": int(user_id),
            "name": structure.names.get(user_id, "Участник"),
            "strength": round(strength[user_id], 2),
            "role": {
                "key": role.role.key,
                "emoji": role.role.emoji,
                "title": role.role.title,
                "reason": role.reason,
            } if role else None,
            "faction": faction_index,
            "color": _hex(faction_color(faction_index) if faction_index else UNAFFILIATED_NODE_COLOR),
        })

    edge_payload = []
    for edge in edges:
        profile = profiles.get((edge.user_a, edge.user_b))
        edge_payload.append({
            "a": int(edge.user_a),
            "b": int(edge.user_b),
            "a_to_b": round(edge.a_to_b, 2),
            "b_to_a": round(edge.b_to_a, 2),
            "total": round(edge.total_weight, 2),
            "genre": profile.keyword if profile else "связь",
        })

    relationships = []
    for view in (await get_relationships(chat_id))[:MAX_RELATIONSHIPS]:
        relationships.append({
            "a": view.user_a_id,
            "b": view.user_b_id,
            "a_name": _without_username_tag(view.user_a_name),
            "b_name": _without_username_tag(view.user_b_name),
            "level": view.level,
            "archetype": view.archetype,
            "affinity": view.affinity,
            "tension": view.tension,
            "reciprocity": round(view.reciprocity, 3),
            "trend": view.trend,
            "positive_reactions": view.positive_reaction_count,
            "negative_reactions": view.negative_reaction_count,
            "events": [str(event.id) for event in view.shared_events],
        })

    try:
        events = await list_events(chat_id, limit=MAX_EVENTS)
    except Exception:
        events = []

    return {
        "chat_id": int(chat_id),
        "period_days": period_days,
        "periods": list(ALLOWED_PERIODS),
        "viewer_id": viewer_id,
        "generated_at": int(time.time()),
        "nodes": nodes,
        "edges": edge_payload,
        "factions": factions,
        "liaison_id": structure.factions.liaison_id,
        "relationships": relationships,
        "chronicle": [_event_payload(chat_id, event) for event in events],
    }
