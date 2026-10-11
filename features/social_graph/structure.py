"""Deterministic social structure: participant roles and factions.

Both are derived only from captured reply/mention/reaction facts.  They turn
raw weights and betweenness into labels people read without a legend: «мост»
instead of «betweenness 0.31», «фракция» instead of a hairball of edges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from features.social_graph.analysis import GraphEdge, _weighted_betweenness, rank_central_participants
from features.social_graph.dynamics import _plural


def _links(count: int) -> str:
    return f"{count} {_plural(count, 'связь', 'связи', 'связей')}"


# --- roles --------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RoleDefinition:
    key: str
    emoji: str
    title: str
    blurb: str


ROLE_DEFINITIONS: dict[str, RoleDefinition] = {
    item.key: item
    for item in (
        RoleDefinition("hub", "👑", "Центр притяжения", "через него проходит больше всего общения"),
        RoleDefinition("bridge", "🌉", "Мост", "связывает компании, которые без него почти не пересекаются"),
        RoleDefinition("pinger", "📣", "Пингер", "предпочитает звать людей по имени"),
        RoleDefinition("reactor", "🎭", "Реакционер", "общается в основном кнопками реакций"),
        RoleDefinition("magnet", "🧲", "Магнит", "к нему обращаются заметно чаще, чем он сам"),
        RoleDefinition("instigator", "🧨", "Заводила", "сам лезет к людям заметно чаще, чем они к нему"),
        RoleDefinition("loner", "🦉", "Одиночка", "держится одной-единственной связью"),
        RoleDefinition("talker", "💬", "Собеседник", "ровно общается без перекосов"),
    )
}

BRIDGE_MIN_BETWEENNESS = 0.2
SKEW_RATIO = 1.8
SKEW_MIN_WEIGHT = 6.0
PINGER_SHARE = 0.4
PINGER_MIN_EVENTS = 3
REACTOR_SHARE = 0.6
REACTOR_MIN_EVENTS = 5


@dataclass(frozen=True, slots=True)
class ParticipantStats:
    user_id: int
    strength: float
    neighbors: int
    betweenness: float
    outgoing: float
    incoming: float
    replies: int
    mentions: int
    reactions: int


@dataclass(frozen=True, slots=True)
class ParticipantRole:
    user_id: int
    role: RoleDefinition
    reason: str
    stats: ParticipantStats

    @property
    def label(self) -> str:
        return f"{self.role.emoji} {self.role.title}"


def participant_stats(
    interactions: Iterable[tuple[int, int, str, float]],
    edges: Sequence[GraphEdge],
) -> dict[int, ParticipantStats]:
    strength: dict[int, float] = {}
    neighbors: dict[int, set[int]] = {}
    for edge in edges:
        for user_id, other in ((edge.user_a, edge.user_b), (edge.user_b, edge.user_a)):
            strength[user_id] = strength.get(user_id, 0.0) + edge.total_weight
            neighbors.setdefault(user_id, set()).add(other)

    outgoing: dict[int, float] = {}
    incoming: dict[int, float] = {}
    by_type: dict[int, dict[str, int]] = {}
    for actor_id, target_id, interaction_type, weight in interactions:
        if actor_id == target_id or weight <= 0:
            continue
        outgoing[actor_id] = outgoing.get(actor_id, 0.0) + float(weight)
        incoming[target_id] = incoming.get(target_id, 0.0) + float(weight)
        counts = by_type.setdefault(actor_id, {})
        counts[interaction_type] = counts.get(interaction_type, 0) + 1

    betweenness = _weighted_betweenness(edges)
    return {
        user_id: ParticipantStats(
            user_id=user_id,
            strength=strength[user_id],
            neighbors=len(neighbors.get(user_id, ())),
            betweenness=betweenness.get(user_id, 0.0),
            outgoing=outgoing.get(user_id, 0.0),
            incoming=incoming.get(user_id, 0.0),
            replies=by_type.get(user_id, {}).get("reply", 0),
            mentions=by_type.get(user_id, {}).get("mention", 0),
            reactions=by_type.get(user_id, {}).get("reaction", 0),
        )
        for user_id in strength
    }


def _role_for(stats: ParticipantStats, *, hub_id: int | None, bridge_ids: set[int]) -> tuple[str, str]:
    actions = stats.replies + stats.mentions + stats.reactions
    if stats.user_id == hub_id:
        return "hub", f"{_links(stats.neighbors)} и самая большая суммарная сила в чате"
    if stats.user_id in bridge_ids:
        return "bridge", f"лежит на коротких путях между участниками, {_links(stats.neighbors)}"
    if stats.mentions >= PINGER_MIN_EVENTS and actions and stats.mentions / actions >= PINGER_SHARE:
        return "pinger", f"{round(stats.mentions / actions * 100)}% его обращений — упоминания"
    if stats.reactions >= REACTOR_MIN_EVENTS and actions and stats.reactions / actions >= REACTOR_SHARE:
        return "reactor", f"{round(stats.reactions / actions * 100)}% его обращений — реакции"
    if stats.incoming >= SKEW_MIN_WEIGHT and stats.incoming >= SKEW_RATIO * max(stats.outgoing, 0.001):
        return "magnet", f"к нему обращаются в {stats.incoming / max(stats.outgoing, 1.0):.1f} раза чаще"
    if stats.outgoing >= SKEW_MIN_WEIGHT and stats.outgoing >= SKEW_RATIO * max(stats.incoming, 0.001):
        return "instigator", f"обращается к другим в {stats.outgoing / max(stats.incoming, 1.0):.1f} раза чаще"
    if stats.neighbors <= 1:
        return "loner", "всего одна связь за период"
    return "talker", f"{_links(stats.neighbors)}, перекосов нет"


def assign_roles(
    interactions: Iterable[tuple[int, int, str, float]],
    edges: Sequence[GraphEdge],
) -> dict[int, ParticipantRole]:
    interactions = tuple(interactions)
    stats = participant_stats(interactions, edges)
    if not stats:
        return {}
    central = rank_central_participants(edges, limit=1)
    hub_id = central[0].user_id if central and central[0].unique_neighbors >= 3 else None
    bridges = sorted(
        (item for item in stats.values() if item.user_id != hub_id),
        key=lambda item: (-item.betweenness, item.user_id),
    )
    bridge_ids = {
        item.user_id
        for item in bridges[:2]
        if item.betweenness >= BRIDGE_MIN_BETWEENNESS and item.neighbors >= 3
    }
    result = {}
    for user_id, item in stats.items():
        key, reason = _role_for(item, hub_id=hub_id, bridge_ids=bridge_ids)
        result[user_id] = ParticipantRole(user_id, ROLE_DEFINITIONS[key], reason, item)
    return result


# --- factions -----------------------------------------------------------------

FACTION_MIN_SIZE = 2


@dataclass(frozen=True, slots=True)
class Faction:
    index: int
    members: tuple[int, ...]
    leader_id: int
    internal_weight: float
    external_weight: float

    @property
    def cohesion(self) -> float:
        total = self.internal_weight + self.external_weight
        return self.internal_weight / total if total > 0 else 0.0


@dataclass(frozen=True, slots=True)
class FactionMap:
    factions: tuple[Faction, ...]
    unaffiliated: tuple[int, ...]
    modularity: float
    membership: dict[int, int]
    liaison_id: int | None


def _modularity(communities: Mapping[int, set[int]], adjacency: Mapping[int, Mapping[int, float]], total: float) -> float:
    if total <= 0:
        return 0.0
    degree = {node: sum(neighbors.values()) for node, neighbors in adjacency.items()}
    score = 0.0
    for members in communities.values():
        internal = sum(
            weight
            for node in members
            for other, weight in adjacency.get(node, {}).items()
            if other in members
        ) / 2.0
        community_degree = sum(degree.get(node, 0.0) for node in members)
        score += internal / total - (community_degree / (2.0 * total)) ** 2
    return score


def detect_factions(edges: Sequence[GraphEdge]) -> FactionMap:
    """Greedy weighted modularity (Clauset–Newman–Moore style), deterministic.

    Small Telegram graphs (tens of nodes) make the quadratic merge loop cheap.
    A chat without real sub-groups stays one faction instead of being split
    arbitrarily, because merges continue only while modularity grows.
    """
    adjacency: dict[int, dict[int, float]] = {}
    for edge in edges:
        weight = edge.total_weight
        if weight <= 0:
            continue
        adjacency.setdefault(edge.user_a, {})[edge.user_b] = weight
        adjacency.setdefault(edge.user_b, {})[edge.user_a] = weight
    total = sum(edge.total_weight for edge in edges if edge.total_weight > 0)
    if not adjacency or total <= 0:
        return FactionMap((), (), 0.0, {}, None)

    degree = {node: sum(neighbors.values()) for node, neighbors in adjacency.items()}
    communities: dict[int, set[int]] = {node: {node} for node in sorted(adjacency)}
    community_degree = {node: degree[node] for node in communities}

    def between(left: set[int], right: set[int]) -> float:
        return sum(adjacency[node].get(other, 0.0) for node in left for other in right)

    while len(communities) > 1:
        best: tuple[float, int, int] | None = None
        keys = sorted(communities)
        for index, left in enumerate(keys):
            for right in keys[index + 1 :]:
                weight = between(communities[left], communities[right])
                if weight <= 0:
                    continue
                delta = weight / total - community_degree[left] * community_degree[right] / (2.0 * total * total)
                candidate = (delta, -left, -right)
                if best is None or candidate > best:
                    best = candidate
        if best is None or best[0] <= 1e-12:
            break
        left, right = -best[1], -best[2]
        communities[left] |= communities.pop(right)
        community_degree[left] += community_degree.pop(right)

    modularity = _modularity(communities, adjacency, total)
    groups = sorted(
        (members for members in communities.values() if len(members) >= FACTION_MIN_SIZE),
        key=lambda members: (-sum(degree[node] for node in members), min(members)),
    )
    unaffiliated = tuple(sorted(
        (node for members in communities.values() if len(members) < FACTION_MIN_SIZE for node in members),
        key=lambda node: (-degree[node], node),
    ))

    factions = []
    membership: dict[int, int] = {}
    for index, members in enumerate(groups, 1):
        ordered = tuple(sorted(members, key=lambda node: (-degree[node], node)))
        internal = between(members, members) / 2.0
        external = sum(degree[node] for node in members) - 2.0 * internal
        factions.append(Faction(index, ordered, ordered[0], internal, external))
        for node in members:
            membership[node] = index

    liaison_id = None
    if len(factions) >= 2:
        cross: dict[int, float] = {}
        for node, neighbors in adjacency.items():
            own = membership.get(node)
            if own is None:
                continue
            cross[node] = sum(
                weight for other, weight in neighbors.items()
                if membership.get(other) is not None and membership[other] != own
            )
        if cross and max(cross.values()) > 0:
            liaison_id = max(cross, key=lambda node: (cross[node], -node))

    return FactionMap(tuple(factions), unaffiliated, modularity, membership, liaison_id)


def fallback_faction_name(faction: Faction, names: Mapping[int, str]) -> str:
    return f"Партия имени {names.get(faction.leader_id, 'Участника')}"
