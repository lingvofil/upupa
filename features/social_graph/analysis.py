"""Deterministic graph analysis for Telegram social interactions."""

from __future__ import annotations

from dataclasses import dataclass
import heapq
from math import inf
from typing import Iterable, Mapping, Sequence


CENTRALITY_STRENGTH_WEIGHT = 0.55
CENTRALITY_BETWEENNESS_WEIGHT = 0.30
CENTRALITY_DIVERSITY_WEIGHT = 0.15


@dataclass(frozen=True)
class GraphEdge:
    user_a: int
    user_b: int
    a_to_b: float
    b_to_a: float

    @property
    def total_weight(self) -> float:
        return self.a_to_b + self.b_to_a

    @property
    def reciprocity(self) -> float:
        total = self.total_weight
        if total <= 0:
            return 0.0
        return (2.0 * min(self.a_to_b, self.b_to_a)) / total


@dataclass(frozen=True)
class PersonalConnection:
    user_id: int
    outgoing: float
    incoming: float

    @property
    def total(self) -> float:
        return self.outgoing + self.incoming

    @property
    def mutual_strength(self) -> float:
        return min(self.outgoing, self.incoming)

    @property
    def reciprocity(self) -> float:
        if self.total <= 0:
            return 0.0
        return (2.0 * self.mutual_strength) / self.total


@dataclass(frozen=True)
class PersonalSummary:
    user_id: int
    total_outgoing: float
    total_incoming: float
    distinct_connections: int
    top_outgoing: tuple[PersonalConnection, ...]
    top_incoming: tuple[PersonalConnection, ...]
    strongest_mutual: tuple[PersonalConnection, ...]
    strongest_asymmetry: PersonalConnection | None


@dataclass(frozen=True)
class CentralityResult:
    user_id: int
    score: float
    weighted_degree: float
    betweenness: float
    unique_neighbors: int
    strong_neighbors: int


@dataclass(frozen=True)
class RenderNode:
    user_id: int
    label: str
    strength: float


@dataclass(frozen=True)
class RenderGraph:
    nodes: tuple[RenderNode, ...]
    edges: tuple[GraphEdge, ...]
    total_node_count: int
    total_edge_count: int


def aggregate_edges(
    interactions: Iterable[tuple[int, int, str, float]],
) -> tuple[GraphEdge, ...]:
    """Collapse directed interaction events into one pair edge with both directions."""
    pairs: dict[tuple[int, int], list[float]] = {}
    for actor_id, target_id, _interaction_type, weight in interactions:
        if actor_id == target_id or weight <= 0:
            continue
        a, b = sorted((actor_id, target_id))
        bucket = pairs.setdefault((a, b), [0.0, 0.0])
        if actor_id == a:
            bucket[0] += float(weight)
        else:
            bucket[1] += float(weight)

    return tuple(
        GraphEdge(a, b, values[0], values[1])
        for (a, b), values in sorted(pairs.items())
        if values[0] + values[1] > 0
    )


def build_personal_summary(
    user_id: int,
    edges: Sequence[GraphEdge],
    *,
    limit: int = 3,
) -> PersonalSummary:
    connections: list[PersonalConnection] = []
    for edge in edges:
        if edge.user_a == user_id:
            connections.append(PersonalConnection(edge.user_b, edge.a_to_b, edge.b_to_a))
        elif edge.user_b == user_id:
            connections.append(PersonalConnection(edge.user_a, edge.b_to_a, edge.a_to_b))

    top_outgoing = tuple(sorted(connections, key=lambda item: (-item.outgoing, -item.total, item.user_id))[:limit])
    top_incoming = tuple(sorted(connections, key=lambda item: (-item.incoming, -item.total, item.user_id))[:limit])
    mutual = [item for item in connections if item.outgoing > 0 and item.incoming > 0]
    strongest_mutual = tuple(
        sorted(mutual, key=lambda item: (-item.mutual_strength, -item.reciprocity, -item.total, item.user_id))[:limit]
    )

    asymmetry_candidates = [
        item
        for item in connections
        if item.total > 0 and abs(item.outgoing - item.incoming) / item.total >= 0.35
    ]
    strongest_asymmetry = None
    if asymmetry_candidates:
        strongest_asymmetry = max(
            asymmetry_candidates,
            key=lambda item: (abs(item.outgoing - item.incoming), item.total, -item.user_id),
        )

    return PersonalSummary(
        user_id=user_id,
        total_outgoing=sum(item.outgoing for item in connections),
        total_incoming=sum(item.incoming for item in connections),
        distinct_connections=len(connections),
        top_outgoing=top_outgoing,
        top_incoming=top_incoming,
        strongest_mutual=strongest_mutual,
        strongest_asymmetry=strongest_asymmetry,
    )


def _weighted_betweenness(edges: Sequence[GraphEdge]) -> dict[int, float]:
    """Brandes betweenness on an undirected weighted graph; stronger edge = shorter distance."""
    adjacency: dict[int, dict[int, float]] = {}
    for edge in edges:
        weight = edge.total_weight
        if weight <= 0:
            continue
        adjacency.setdefault(edge.user_a, {})[edge.user_b] = weight
        adjacency.setdefault(edge.user_b, {})[edge.user_a] = weight

    nodes = sorted(adjacency)
    centrality = {node: 0.0 for node in nodes}

    for source in nodes:
        stack: list[int] = []
        predecessors: dict[int, list[int]] = {node: [] for node in nodes}
        sigma = {node: 0.0 for node in nodes}
        sigma[source] = 1.0
        distance = {node: inf for node in nodes}
        distance[source] = 0.0
        queue: list[tuple[float, int]] = [(0.0, source)]

        while queue:
            dist_v, v = heapq.heappop(queue)
            if dist_v > distance[v] + 1e-12:
                continue
            stack.append(v)
            for w, strength in adjacency[v].items():
                candidate = dist_v + (1.0 / strength)
                if candidate < distance[w] - 1e-12:
                    distance[w] = candidate
                    heapq.heappush(queue, (candidate, w))
                    sigma[w] = sigma[v]
                    predecessors[w] = [v]
                elif abs(candidate - distance[w]) <= 1e-12:
                    sigma[w] += sigma[v]
                    predecessors[w].append(v)

        dependency = {node: 0.0 for node in nodes}
        while stack:
            w = stack.pop()
            if sigma[w] > 0:
                factor = (1.0 + dependency[w]) / sigma[w]
                for v in predecessors[w]:
                    dependency[v] += sigma[v] * factor
            if w != source:
                centrality[w] += dependency[w]

    for node in centrality:
        centrality[node] /= 2.0

    n = len(nodes)
    if n > 2:
        scale = 2.0 / ((n - 1) * (n - 2))
        for node in centrality:
            centrality[node] *= scale
    return centrality


def rank_central_participants(
    edges: Sequence[GraphEdge],
    *,
    strong_edge_threshold: float = 3.0,
    limit: int = 5,
) -> tuple[CentralityResult, ...]:
    weighted_degree: dict[int, float] = {}
    neighbors: dict[int, set[int]] = {}
    strong_neighbors: dict[int, int] = {}

    for edge in edges:
        total = edge.total_weight
        weighted_degree[edge.user_a] = weighted_degree.get(edge.user_a, 0.0) + total
        weighted_degree[edge.user_b] = weighted_degree.get(edge.user_b, 0.0) + total
        neighbors.setdefault(edge.user_a, set()).add(edge.user_b)
        neighbors.setdefault(edge.user_b, set()).add(edge.user_a)
        if total >= strong_edge_threshold:
            strong_neighbors[edge.user_a] = strong_neighbors.get(edge.user_a, 0) + 1
            strong_neighbors[edge.user_b] = strong_neighbors.get(edge.user_b, 0) + 1

    if not weighted_degree:
        return ()

    betweenness = _weighted_betweenness(edges)
    max_strength = max(weighted_degree.values()) or 1.0
    max_diversity = max((len(value) for value in neighbors.values()), default=1) or 1
    max_betweenness = max(betweenness.values(), default=0.0)

    results = []
    for user_id, strength in weighted_degree.items():
        strength_norm = strength / max_strength
        diversity_norm = len(neighbors.get(user_id, ())) / max_diversity
        betweenness_norm = betweenness.get(user_id, 0.0) / max_betweenness if max_betweenness > 0 else 0.0
        score = (
            CENTRALITY_STRENGTH_WEIGHT * strength_norm
            + CENTRALITY_BETWEENNESS_WEIGHT * betweenness_norm
            + CENTRALITY_DIVERSITY_WEIGHT * diversity_norm
        )
        results.append(
            CentralityResult(
                user_id=user_id,
                score=score,
                weighted_degree=strength,
                betweenness=betweenness.get(user_id, 0.0),
                unique_neighbors=len(neighbors.get(user_id, ())),
                strong_neighbors=strong_neighbors.get(user_id, 0),
            )
        )

    return tuple(
        sorted(
            results,
            key=lambda item: (-item.score, -item.weighted_degree, -item.unique_neighbors, item.user_id),
        )[:limit]
    )


def select_render_graph(
    edges: Sequence[GraphEdge],
    names: Mapping[int, str],
    *,
    max_nodes: int = 18,
    max_edges: int = 32,
) -> RenderGraph:
    strength: dict[int, float] = {}
    for edge in edges:
        strength[edge.user_a] = strength.get(edge.user_a, 0.0) + edge.total_weight
        strength[edge.user_b] = strength.get(edge.user_b, 0.0) + edge.total_weight

    selected_ids = {
        user_id
        for user_id, _value in sorted(strength.items(), key=lambda item: (-item[1], item[0]))[:max_nodes]
    }
    selected_edges = [
        edge for edge in edges if edge.user_a in selected_ids and edge.user_b in selected_ids
    ]
    selected_edges.sort(key=lambda edge: (-edge.total_weight, edge.user_a, edge.user_b))
    selected_edges = selected_edges[:max_edges]

    visible_ids = {node for edge in selected_edges for node in (edge.user_a, edge.user_b)}
    nodes = tuple(
        RenderNode(user_id=user_id, label=names.get(user_id, "Участник"), strength=strength[user_id])
        for user_id in sorted(visible_ids, key=lambda uid: (-strength[uid], uid))
    )

    return RenderGraph(
        nodes=nodes,
        edges=tuple(selected_edges),
        total_node_count=len(strength),
        total_edge_count=len(edges),
    )
