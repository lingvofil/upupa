"""Deterministic interaction-type analysis for the rendered social graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from features.social_graph.analysis import GraphEdge, RenderGraph


INTERACTION_TYPES = ("reply", "mention", "reaction")
EDGE_ASYMMETRY_RATIO = 1.75
MIXED_INTERACTION_SHARE_GAP = 0.12


@dataclass(frozen=True)
class EdgeInteractionProfile:
    user_a: int
    user_b: int
    reply_a_to_b: float = 0.0
    reply_b_to_a: float = 0.0
    mention_a_to_b: float = 0.0
    mention_b_to_a: float = 0.0
    reaction_a_to_b: float = 0.0
    reaction_b_to_a: float = 0.0

    def direction(self, interaction_type: str) -> tuple[float, float]:
        if interaction_type not in INTERACTION_TYPES:
            return 0.0, 0.0
        return (
            float(getattr(self, f"{interaction_type}_a_to_b")),
            float(getattr(self, f"{interaction_type}_b_to_a")),
        )

    @property
    def type_totals(self) -> dict[str, float]:
        return {interaction_type: sum(self.direction(interaction_type)) for interaction_type in INTERACTION_TYPES}

    @property
    def shares(self) -> dict[str, float]:
        totals = self.type_totals
        total = sum(totals.values())
        if total <= 0:
            return {interaction_type: 0.0 for interaction_type in INTERACTION_TYPES}
        return {interaction_type: totals[interaction_type] / total for interaction_type in INTERACTION_TYPES}

    @property
    def dominant_type(self) -> str | None:
        shares = self.shares
        ranked = sorted(shares.items(), key=lambda item: (-item[1], INTERACTION_TYPES.index(item[0])))
        if not ranked or ranked[0][1] <= 0:
            return None
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] <= MIXED_INTERACTION_SHARE_GAP:
            return "mixed"
        return ranked[0][0]

    @property
    def keyword(self) -> str:
        if self.dominant_type == "mixed":
            return "винегрет"
        if self.dominant_type == "mention":
            return "пинги"
        if self.dominant_type == "reaction":
            return "реакты"
        if self.dominant_type == "reply":
            forward, backward = self.direction("reply")
            return "доёб" if _is_asymmetric(forward, backward) else "срач"
        return "связь"


def _edge_key(user_a: int, user_b: int) -> tuple[int, int]:
    return tuple(sorted((user_a, user_b)))


def _is_asymmetric(forward: float, backward: float) -> bool:
    high = max(forward, backward)
    low = min(forward, backward)
    return high > 0 and high >= EDGE_ASYMMETRY_RATIO * max(low, 0.001)


def build_edge_interaction_profiles(
    interactions: Iterable[tuple[int, int, str, float]],
    visible_edges: Sequence[GraphEdge],
) -> dict[tuple[int, int], EdgeInteractionProfile]:
    """Aggregate real captured interactions only for the edges visible in the picture."""
    visible_keys = {_edge_key(edge.user_a, edge.user_b) for edge in visible_edges}
    buckets: dict[tuple[int, int], dict[str, float]] = {
        key: {
            "reply_a_to_b": 0.0,
            "reply_b_to_a": 0.0,
            "mention_a_to_b": 0.0,
            "mention_b_to_a": 0.0,
            "reaction_a_to_b": 0.0,
            "reaction_b_to_a": 0.0,
        }
        for key in visible_keys
    }

    for actor_id, target_id, interaction_type, weight in interactions:
        if actor_id == target_id or weight <= 0 or interaction_type not in INTERACTION_TYPES:
            continue
        key = _edge_key(actor_id, target_id)
        if key not in buckets:
            continue
        a, _b = key
        direction = "a_to_b" if actor_id == a else "b_to_a"
        buckets[key][f"{interaction_type}_{direction}"] += float(weight)

    return {
        key: EdgeInteractionProfile(user_a=key[0], user_b=key[1], **bucket)
        for key, bucket in buckets.items()
    }


def edge_keywords(profiles: Mapping[tuple[int, int], EdgeInteractionProfile]) -> dict[tuple[int, int], str]:
    return {key: profile.keyword for key, profile in profiles.items()}


def _share_summary(profile: EdgeInteractionProfile) -> str:
    shares = profile.shares
    return (
        f"реплаи {round(shares['reply'] * 100)}%, "
        f"пинги {round(shares['mention'] * 100)}%, "
        f"реакты {round(shares['reaction'] * 100)}%"
    )


def _edge_direction(edge: GraphEdge, names: Mapping[int, str]) -> str:
    name_a = names.get(edge.user_a, "Участник")
    name_b = names.get(edge.user_b, "Участник")
    if _is_asymmetric(edge.a_to_b, edge.b_to_a):
        return f"{name_a} → {name_b}" if edge.a_to_b >= edge.b_to_a else f"{name_b} → {name_a}"
    return f"{name_a} ↔ {name_b}"


def _dominant_direction(profile: EdgeInteractionProfile, interaction_type: str) -> tuple[int | None, int | None]:
    forward, backward = profile.direction(interaction_type)
    if not _is_asymmetric(forward, backward):
        return None, None
    if forward >= backward:
        return profile.user_a, profile.user_b
    return profile.user_b, profile.user_a


def describe_graph_edge(
    edge: GraphEdge,
    profile: EdgeInteractionProfile,
    names: Mapping[int, str],
) -> str:
    """Describe only observable messaging mechanics, never inferred offline relationships."""
    keyword = profile.keyword
    shares = _share_summary(profile)

    if keyword == "срач":
        commentary = "реплаятся как ненормальные и примерно на равных."
    elif keyword == "доёб":
        actor_id, target_id = _dominant_direction(profile, "reply")
        actor = names.get(actor_id, "Кто-то") if actor_id is not None else "Кто-то"
        target = names.get(target_id, "кого-то") if target_id is not None else "кого-то"
        commentary = f"реплаи с перекосом: {actor} заметно чаще лезет с ответами к {target}."
    elif keyword == "пинги":
        actor_id, target_id = _dominant_direction(profile, "mention")
        if actor_id is None:
            commentary = "дёргают друг друга по имени; написать без пинга, видимо, западло."
        else:
            actor = names.get(actor_id, "Кто-то")
            target = names.get(target_id, "кого-то")
            commentary = f"{actor} заметно чаще дёргает {target} по имени."
    elif keyword == "реакты":
        actor_id, target_id = _dominant_direction(profile, "reaction")
        if actor_id is None:
            commentary = "молча обкидываются реакциями, слова сегодня не завезли."
        else:
            actor = names.get(actor_id, "Кто-то")
            target = names.get(target_id, "кого-то")
            commentary = f"{actor} заметно чаще закидывает {target} реакциями вместо лишних букв."
    elif keyword == "винегрет":
        commentary = "коммуникационный винегрет: всё намешали в одну статистическую помойку."
    else:
        commentary = "связь в графе есть, а нормального типа взаимодействия в данных не нашлось."

    return f"• {_edge_direction(edge, names)} — {keyword}: {shares}; {commentary}"


def build_cringe_graph_explanation(
    graph: RenderGraph,
    profiles: Mapping[tuple[int, int], EdgeInteractionProfile],
    names: Mapping[int, str],
) -> str:
    lines = ["Что тут за хуйня по связям:"]
    for edge in graph.edges:
        key = _edge_key(edge.user_a, edge.user_b)
        profile = profiles.get(key, EdgeInteractionProfile(*key))
        lines.append(describe_graph_edge(edge, profile, names))
    return "\n".join(lines)
