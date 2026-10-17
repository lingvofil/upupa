"""Long-lived relationship layer built on top of deterministic social interactions.

The social graph records facts (reply/mention/reaction).  This module turns the
facts plus grounded Chronicle events into a pair-level relationship view.

Scoring model v2:

* Близость — recent, decaying activity (daily XP with a ~45-day time constant)
  on a log scale relative to the chat's most active pair, plus reciprocity
  (discounted for tiny samples), shared Chronicle history and variety.
* Напряжение — conflicts *between the two people*: a Chronicle conflict counts
  for a pair only if they were opponents in it.  The Chronicle AI names the
  opponent pairs (``ai_metadata["conflict_pairs"]``); that list is final.  Only
  for older, not yet labelled episodes we fall back to guessing combatants from
  the pairs with the most mutual replies/mentions in its window.  Episodes
  older than raw-data retention count only for 2–3 person episodes.  Negative
  reactions (🤡, 👎, 💩 …) add at most a third of the scale on their own.
  Raw swearing is never a signal.
* Уровень (0–100) — the pair's place among all pairs of the same chat
  (percentile of an unclamped score, so pairs no longer tie at the top)
  blended with absolute closeness; ``rank`` is the place in the chat.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Iterable

from features.chronicle.models import ChronicleEvent
from features.chronicle.runtime import list_events as list_chronicle_events
from features.social_graph.service import (
    is_social_graph_enabled,
    list_active_chats,
    load_chat_daily,
    load_pair_exchanges,
    load_relationship_snapshots,
    load_relationship_states,
    save_relationship_snapshot,
)


CHRONICLE_EVENT_LIMIT = 300
SNAPSHOT_INTERVAL_SECONDS = 6 * 3600
SNAPSHOT_ACTIVE_DAYS = 30
# Reactions are a weaker conflict signal than a Chronicle-confirmed conflict:
# on their own they can never reach «заклятые друзья» (tension ≥ 50).
REACTION_TENSION_CAP = 35.0
REACTION_TENSION_MIN_NEGATIVE = 3
LEVEL_MAX = 100
# A pair is a combatant of an episode if its mutual exchanges are at least this
# share of the busiest pair in the episode window (and at least 2).
COMBATANT_SHARE = 0.5
COMBATANT_MIN_EXCHANGES = 2
# …and its share of the episode's exchanges must clearly exceed its usual share
# of the chat's exchanges: a core pair that always talks is not «fighting».
COMBATANT_SPIKE_RATIO = 2.0
COMBATANT_SPIKE_MARGIN = 0.25
BASELINE_KEY = "__baseline__"
BASELINE_DAYS = 30
OLD_CONFLICT_MAX_PARTICIPANTS = 3
ACTIVITY_DECAY_DAYS = 45.0
ACTIVITY_LOOKBACK_DAYS = 180
ACTIVITY_SCALE_FLOOR = 30.0
# Raw interactions are kept 90 days; older conflicts cannot be checked pair by pair.
EXCHANGE_EVIDENCE_DAYS = 85
EXCHANGE_PADDING_BEFORE = timedelta(minutes=10)
EXCHANGE_PADDING_AFTER = timedelta(minutes=15)
SYNTHETIC_PAIR_MAX_PARTICIPANTS = 3
LEVEL_PERCENTILE_WEIGHT = 0.7
# Snapshots captured before the v2 model are not comparable with new levels.
SCORING_MODEL_EPOCH = datetime(2026, 10, 11, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class RelationshipView:
    chat_id: int
    user_a_id: int
    user_b_id: int
    user_a_name: str
    user_b_name: str
    affinity: int
    tension: int
    xp: float
    level: int
    reciprocity: float
    archetype: str
    trend: str
    interaction_count: int
    reply_count: int
    mention_count: int
    reaction_count: int
    shared_event_count: int
    a_to_b_count: int
    b_to_a_count: int
    recent_7d: int
    previous_21d: int
    last_interaction_at: datetime | None
    shared_events: tuple[ChronicleEvent, ...]
    positive_reaction_count: int = 0
    negative_reaction_count: int = 0
    score: float = 0.0
    rank: int = 0
    rank_total: int = 0
    conflict_episodes: int = 0

    @property
    def strength(self) -> float:
        return self.affinity * 0.6 + self.tension * 0.4


@dataclass(frozen=True, slots=True)
class RelationshipSnapshot:
    captured_at: datetime
    affinity: int
    tension: int
    xp: float
    level: int
    reciprocity: float
    archetype: str
    trend: str


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _event_decay(event: ChronicleEvent, now: datetime) -> float:
    age_days = max(0.0, (now - event.event_ended_at).total_seconds() / 86400.0)
    # Landmark events matter for months, but slowly stop dominating the present.
    return 0.35 + 0.65 * math.exp(-age_days / 90.0)


def _pair_events(events: Iterable[ChronicleEvent], user_a_id: int, user_b_id: int) -> tuple[ChronicleEvent, ...]:
    pair = {int(user_a_id), int(user_b_id)}
    return tuple(event for event in events if pair.issubset(set(event.participant_ids)))


def _reciprocity(a_to_b_weight: float, b_to_a_weight: float) -> float:
    high = max(a_to_b_weight, b_to_a_weight)
    if high <= 0:
        return 0.0
    return _clamp(min(a_to_b_weight, b_to_a_weight) / high, 0.0, 1.0)


def _trend(recent_7d: int, previous_21d: int, tension: int, last_interaction_at: datetime | None, now: datetime) -> str:
    baseline_week = previous_21d / 3.0
    if previous_21d == 0 and recent_7d >= 3:
        return "внезапно возобновили общение"
    if recent_7d >= 4 and recent_7d >= max(2.0, baseline_week * 1.6):
        return "конфликт набирает обороты" if tension >= 45 else "резко сближаются"
    if last_interaction_at is not None and (now - last_interaction_at).days >= 14:
        return "исчезли с радаров"
    if baseline_week >= 2 and recent_7d <= baseline_week * 0.45:
        return "остывают"
    return "отношения стабильны"


def _archetype(
    *,
    affinity: int,
    tension: int,
    reciprocity: float,
    interaction_count: int,
    events: tuple[ChronicleEvent, ...],
    positive_reactions: int = 0,
    negative_reactions: int = 0,
) -> str:
    categories = [event.category for event in events]
    if interaction_count >= 6 and reciprocity < 0.35:
        return "односторонний сериал"
    if affinity >= 65 and tension >= 50:
        return "заклятые друзья"
    if tension >= 60 and affinity < 50:
        return "взаимная аллергия"
    if negative_reactions >= 4 and negative_reactions >= positive_reactions and tension >= 20:
        return "взаимный троллинг"
    if categories.count("game") >= 2 and affinity >= 35:
        return "игровые союзники"
    meme_count = sum(category in {"funny", "absurd", "local_meme"} for category in categories)
    if meme_count >= 2 and affinity >= 40:
        return "мемный дуэт"
    if tension >= 35:
        return "спорщики"
    if affinity >= 70:
        return "стабильный дуэт"
    if affinity >= 40:
        return "регулярные собеседники"
    return "периодически пересекаются"


def _group_share(event: ChronicleEvent) -> float:
    """A group episode says little about any single pair inside it."""
    return min(1.0, 2.0 / max(2, len(set(event.participant_ids))))


def conflict_pairs(event: ChronicleEvent) -> set[tuple[int, int]] | None:
    """Opponent pairs named by the Chronicle AI; None if the episode is unlabelled."""
    raw = (event.ai_metadata or {}).get("conflict_pairs") if isinstance(event.ai_metadata, dict) else None
    if not isinstance(raw, list):
        return None
    pairs: set[tuple[int, int]] = set()
    for item in raw:
        try:
            left, right = int(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if left != right:
            pairs.add((min(left, right), max(left, right)))
    return pairs


def _conflict_factor(event: ChronicleEvent, pair: tuple[int, int], exchanges: dict[str, dict[tuple[int, int], int]]) -> float:
    """How much of a Chronicle conflict belongs to this particular pair."""
    labelled = conflict_pairs(event)
    if labelled is not None:
        # Reply volume says who the thread was *about*, not who fought: a
        # thread around one person makes every spoke to them look like a
        # «combatant».  An explicit opponent list wins.
        return 1.0 if pair in labelled else 0.0
    participants = set(event.participant_ids)
    evidence = exchanges.get(str(event.id))
    if evidence is None:
        # No raw interactions left: trust only small, clearly two-sided episodes.
        if len(participants) > OLD_CONFLICT_MAX_PARTICIPANTS:
            return 0.0
        return _group_share(event)
    direct = int(evidence.get(pair, 0))
    in_episode = {key: value for key, value in evidence.items() if set(key).issubset(participants)} or evidence
    busiest = max(in_episode.values(), default=0)
    if direct < COMBATANT_MIN_EXCHANGES or busiest <= 0 or direct < COMBATANT_SHARE * busiest:
        return 0.0
    baseline = exchanges.get(BASELINE_KEY)
    if baseline:
        window_share = direct / max(1, sum(evidence.values()))
        usual_share = baseline.get(pair, 0) / max(1, sum(baseline.values()))
        needed = min(COMBATANT_SPIKE_RATIO * usual_share, usual_share + COMBATANT_SPIKE_MARGIN)
        if window_share < needed:
            return 0.0
    return min(1.0, direct / busiest)


def _calculate_view(
    chat_id: int,
    row: dict,
    events: Iterable[ChronicleEvent],
    now: datetime,
    *,
    recent_xp: float | None = None,
    xp_scale: float = ACTIVITY_SCALE_FLOOR,
    exchanges: dict[str, dict[tuple[int, int], int]] | None = None,
) -> RelationshipView:
    user_a_id = int(row["user_a_id"])
    user_b_id = int(row["user_b_id"])
    pair = (min(user_a_id, user_b_id), max(user_a_id, user_b_id))
    shared = _pair_events(events, user_a_id, user_b_id)
    exchanges = exchanges or {}

    a_weight = float(row.get("a_to_b_weight", 0.0) or 0.0)
    b_weight = float(row.get("b_to_a_weight", 0.0) or 0.0)
    reciprocity = _reciprocity(a_weight, b_weight)
    base_xp = float(row.get("xp", 0.0) or 0.0)

    shared_signal = 0.0
    conflict_signal = 0.0
    conflict_episodes = 0
    event_xp = 0.0
    for event in shared:
        decay = _event_decay(event, now)
        importance = max(0.0, float(event.importance_score))
        shared_signal += importance * decay * min(1.0, 1.5 * _group_share(event))
        event_xp += min(15.0, 2.0 + importance * 0.8) * _group_share(event)
        if event.category == "conflict":
            factor = _conflict_factor(event, pair, exchanges)
            if factor > 0:
                conflict_episodes += 1
                conflict_signal += importance * decay * factor

    interaction_count = int(row.get("interaction_count", 0) or 0)
    reply_count = int(row.get("reply_count", 0) or 0)
    mention_count = int(row.get("mention_count", 0) or 0)
    reaction_count = int(row.get("reaction_count", 0) or 0)
    last_interaction_at = _utc(row.get("last_interaction_at"))

    if recent_xp is None:
        idle_days = (now - last_interaction_at).total_seconds() / 86400.0 if last_interaction_at else ACTIVITY_LOOKBACK_DAYS
        recent_xp = max(0.0, base_xp) * math.exp(-max(0.0, idle_days) / ACTIVITY_DECAY_DAYS)
    scale = max(ACTIVITY_SCALE_FLOOR, float(xp_scale))
    # Square root keeps visible differences between busy pairs (a log curve put
    # every active pair at ~100).
    activity = 100.0 * math.sqrt(min(1.0, max(0.0, recent_xp) / scale))
    event_affinity = 100.0 * (1.0 - math.exp(-shared_signal / 32.0))
    variety = sum(value > 0 for value in (reply_count, mention_count, reaction_count)) / 3.0 * 100.0
    # 1:1 from two messages is not «perfect reciprocity» yet.
    reciprocity_confidence = min(1.0, interaction_count / 12.0)
    affinity = (
        activity * 0.55
        + reciprocity * reciprocity_confidence * 100.0 * 0.25
        + event_affinity * 0.15
        + variety * 0.05
    )

    positive_reactions = int(row.get("positive_reaction_count", 0) or 0)
    negative_reactions = int(row.get("negative_reaction_count", 0) or 0)
    valenced = positive_reactions + negative_reactions
    negative_share = negative_reactions / valenced if valenced else 0.0
    # Warm reactions add a little closeness; it is already partly counted in xp.
    affinity += min(6.0, positive_reactions * 0.3) * (1.0 - negative_share)

    chronicle_tension = 1.0 - math.exp(-conflict_signal / 18.0)
    reaction_tension = 0.0
    if negative_reactions >= REACTION_TENSION_MIN_NEGATIVE:
        reaction_tension = (
            REACTION_TENSION_CAP / 100.0
            * (1.0 - math.exp(-negative_reactions / 10.0))
            * negative_share
        )
    # Independent signals combine like probabilities and never exceed 100.
    tension = 100.0 * (1.0 - (1.0 - chronicle_tension) * (1.0 - reaction_tension))
    affinity_i = int(round(_clamp(affinity)))
    tension_i = int(round(_clamp(tension)))
    total_xp = base_xp + event_xp

    # Unclamped score: two pairs at «100» must still be comparable.
    score = _level_strength(affinity, total_xp)
    # Provisional absolute level; get_relationships() re-ranks pairs within the chat.
    level = int(round(_clamp(score, 0.0, 100.0) / 100.0 * LEVEL_MAX))

    recent_7d = int(row.get("recent_7d", 0) or 0)
    previous_21d = int(row.get("previous_21d", 0) or 0)
    trend = _trend(recent_7d, previous_21d, tension_i, last_interaction_at, now)
    archetype = _archetype(
        affinity=affinity_i,
        tension=tension_i,
        reciprocity=reciprocity,
        interaction_count=interaction_count,
        events=shared,
        positive_reactions=positive_reactions,
        negative_reactions=negative_reactions,
    )

    return RelationshipView(
        chat_id=int(chat_id),
        user_a_id=user_a_id,
        user_b_id=user_b_id,
        user_a_name=str(row.get("user_a_name") or "Участник"),
        user_b_name=str(row.get("user_b_name") or "Участник"),
        affinity=affinity_i,
        tension=tension_i,
        xp=total_xp,
        level=level,
        reciprocity=reciprocity,
        archetype=archetype,
        trend=trend,
        interaction_count=interaction_count,
        reply_count=reply_count,
        mention_count=mention_count,
        reaction_count=reaction_count,
        shared_event_count=len(shared),
        a_to_b_count=int(row.get("a_to_b_count", 0) or 0),
        b_to_a_count=int(row.get("b_to_a_count", 0) or 0),
        recent_7d=recent_7d,
        previous_21d=previous_21d,
        last_interaction_at=last_interaction_at,
        shared_events=shared,
        positive_reaction_count=positive_reactions,
        negative_reaction_count=negative_reactions,
        score=score,
        conflict_episodes=conflict_episodes,
    )


def _level_strength(affinity: float, total_xp: float) -> float:
    """Closeness plus a small bonus for a long shared history; tension is not «strength»."""
    return affinity + min(8.0, math.log1p(max(0.0, total_xp)) * 1.2)


def _percentiles(values: list[float]) -> list[float]:
    """Average-rank percentile in [0, 1]; equal values share a rank."""
    count = len(values)
    if count <= 1:
        return [1.0] * count
    order = sorted(range(count), key=lambda index: values[index])
    result = [0.0] * count
    position = 0
    while position < count:
        tail = position
        while tail + 1 < count and values[order[tail + 1]] == values[order[position]]:
            tail += 1
        rank = (position + tail) / 2.0
        for index in order[position : tail + 1]:
            result[index] = rank / (count - 1)
        position = tail + 1
    return result


def assign_relative_levels(views: list[RelationshipView]) -> list[RelationshipView]:
    """Rank pairs within the chat: 100 means «the top of this chat», rank is the place."""
    scores = [view.score for view in views]
    percentiles = _percentiles(scores)
    order = sorted(range(len(views)), key=lambda index: (-scores[index], views[index].user_a_id, views[index].user_b_id))
    ranks = {index: place for place, index in enumerate(order, 1)}
    result = []
    for index, (view, percentile) in enumerate(zip(views, percentiles)):
        absolute = _clamp(view.score, 0.0, 100.0) / 100.0
        blended = LEVEL_PERCENTILE_WEIGHT * percentile + (1.0 - LEVEL_PERCENTILE_WEIGHT) * absolute
        level = int(round(_clamp(blended * LEVEL_MAX, 0.0, LEVEL_MAX)))
        if view.interaction_count > 0 or view.shared_event_count > 0:
            level = max(1, level)
        result.append(replace(view, level=level, rank=ranks[index], rank_total=len(views)))
    return result


def _recent_xp_by_pair(rows: list[dict], now: datetime) -> dict[tuple[int, int], float]:
    today = now.date()
    result: dict[tuple[int, int], float] = {}
    for row in rows:
        try:
            day = datetime.fromisoformat(str(row["activity_date"])[:10]).date()
        except (KeyError, TypeError, ValueError):
            continue
        age = max(0, (today - day).days)
        pair = (int(row["user_a_id"]), int(row["user_b_id"]))
        xp = float(row.get("xp_earned") or 0.0)
        result[pair] = result.get(pair, 0.0) + xp * math.exp(-age / ACTIVITY_DECAY_DAYS)
    return result


async def _load_recent_xp(chat_id: int, now: datetime) -> dict[tuple[int, int], float]:
    since = (now - timedelta(days=ACTIVITY_LOOKBACK_DAYS)).date().isoformat()
    try:
        rows = await load_chat_daily(chat_id, since)
    except Exception as exc:
        logging.warning("[social_graph] daily activity unavailable chat=%s: %s", chat_id, exc)
        return {}
    return _recent_xp_by_pair(rows, now)


async def _load_conflict_exchanges(
    chat_id: int,
    events: Iterable[ChronicleEvent],
    now: datetime,
) -> dict[str, dict[tuple[int, int], int]]:
    cutoff = now - timedelta(days=EXCHANGE_EVIDENCE_DAYS)
    windows = [
        (str(event.id), event.event_started_at - EXCHANGE_PADDING_BEFORE, event.event_ended_at + EXCHANGE_PADDING_AFTER)
        for event in events
        if event.category == "conflict" and event.event_ended_at >= cutoff and conflict_pairs(event) is None
    ]
    if not windows:
        return {}
    windows.append((BASELINE_KEY, now - timedelta(days=BASELINE_DAYS), now))
    try:
        return await load_pair_exchanges(chat_id, windows)
    except Exception as exc:
        # Unknown evidence falls back to the 2/n group share, never to «everyone fought».
        logging.warning("[social_graph] conflict evidence unavailable chat=%s: %s", chat_id, exc)
        return {}


def _synthetic_rows_from_events(events: Iterable[ChronicleEvent], existing: set[tuple[int, int]]) -> list[dict]:
    rows: list[dict] = []
    seen = set(existing)
    for event in events:
        people = list(zip(event.participant_ids, event.participant_names))
        if len(people) > SYNTHETIC_PAIR_MAX_PARTICIPANTS:
            # A crowded episode is not evidence of a relationship between every pair in it.
            continue
        for index, (left_id, left_name) in enumerate(people):
            for right_id, right_name in people[index + 1 :]:
                user_a_id, user_b_id = sorted((int(left_id), int(right_id)))
                key = (user_a_id, user_b_id)
                if key in seen:
                    continue
                seen.add(key)
                names = {int(left_id): left_name, int(right_id): right_name}
                rows.append({
                    "user_a_id": user_a_id,
                    "user_b_id": user_b_id,
                    "user_a_name": names.get(user_a_id) or "Участник",
                    "user_b_name": names.get(user_b_id) or "Участник",
                    "xp": 0.0,
                    "interaction_count": 0,
                    "reply_count": 0,
                    "mention_count": 0,
                    "reaction_count": 0,
                    "a_to_b_count": 0,
                    "b_to_a_count": 0,
                    "a_to_b_weight": 0.0,
                    "b_to_a_weight": 0.0,
                    "recent_7d": 0,
                    "previous_21d": 0,
                    "last_interaction_at": None,
                })
    return rows


async def get_relationships(chat_id: int, *, user_id: int | None = None, persist_snapshots: bool = False) -> list[RelationshipView]:
    """Compute pair views.

    Reads are side-effect free by default: snapshots are captured on a fixed
    schedule by ``relationship_snapshot_loop`` so that history reflects time,
    not the moments somebody happened to call a command.
    """
    # Levels are relative to the whole chat, so all pairs are scored even for one user.
    now = datetime.now(timezone.utc)
    rows, events, recent_xp = await asyncio.gather(
        load_relationship_states(chat_id),
        list_chronicle_events(chat_id, limit=CHRONICLE_EVENT_LIMIT),
        _load_recent_xp(chat_id, now),
    )
    exchanges = await _load_conflict_exchanges(chat_id, events, now)
    existing = {(int(row["user_a_id"]), int(row["user_b_id"])) for row in rows}
    rows = [*rows, *_synthetic_rows_from_events(events, existing)]

    xp_scale = max([ACTIVITY_SCALE_FLOOR, *recent_xp.values()])
    views = [
        _calculate_view(
            chat_id,
            row,
            events,
            now,
            recent_xp=recent_xp.get((int(row["user_a_id"]), int(row["user_b_id"]))) if recent_xp else None,
            xp_scale=xp_scale,
            exchanges=exchanges,
        )
        for row in rows
    ]
    views = assign_relative_levels(views)
    if user_id is not None:
        views = [view for view in views if int(user_id) in {view.user_a_id, view.user_b_id}]
    views.sort(key=lambda item: (item.level, item.strength, item.xp), reverse=True)

    if persist_snapshots and views:
        await asyncio.gather(*(save_relationship_snapshot(view) for view in views))
    return views


async def get_relationship(chat_id: int, user_a_id: int, user_b_id: int) -> RelationshipView | None:
    if int(user_a_id) == int(user_b_id):
        return None
    pair = {int(user_a_id), int(user_b_id)}
    views = await get_relationships(chat_id)
    return next((view for view in views if {view.user_a_id, view.user_b_id} == pair), None)


async def get_relationship_history(chat_id: int, user_a_id: int, user_b_id: int) -> tuple[RelationshipView | None, list[RelationshipSnapshot]]:
    view = await get_relationship(chat_id, user_a_id, user_b_id)
    rows = await load_relationship_snapshots(chat_id, user_a_id, user_b_id, limit=20)
    snapshots = [
        RelationshipSnapshot(
            captured_at=_utc(row.get("captured_at")) or datetime.now(timezone.utc),
            affinity=int(row.get("affinity", 0)),
            tension=int(row.get("tension", 0)),
            xp=float(row.get("xp", 0.0)),
            level=int(row.get("level", 0)),
            reciprocity=float(row.get("reciprocity", 0.0)),
            archetype=str(row.get("archetype") or "периодически пересекаются"),
            trend=str(row.get("trend") or "отношения стабильны"),
        )
        for row in rows
    ]
    # Levels before the v2 model were absolute and inflated; comparing them with
    # relative levels would invent a «level drop» for every pair.
    snapshots = [snapshot for snapshot in snapshots if snapshot.captured_at >= SCORING_MODEL_EPOCH]
    return view, snapshots


async def capture_relationship_snapshots(*, active_days: int = SNAPSHOT_ACTIVE_DAYS) -> int:
    """Persist one snapshot per pair for every recently active chat."""
    total = 0
    for chat_id in await list_active_chats(days=active_days):
        if not is_social_graph_enabled(chat_id):
            continue
        try:
            views = await get_relationships(chat_id, persist_snapshots=True)
        except Exception as exc:
            logging.warning("[social_graph] relationship snapshot failed chat=%s: %s", chat_id, exc)
            continue
        total += len(views)
    return total


async def run_reaction_valence_backfill() -> int | None:
    """One-off recovery of reaction emoji captured by the Chronicle before valence counting."""
    from core.paths import HISTORY_DB_PATH
    from features.social_graph.service import backfill_reaction_valence

    try:
        counted = await asyncio.to_thread(backfill_reaction_valence, HISTORY_DB_PATH)
    except Exception as exc:
        logging.warning("[social_graph] reaction valence backfill failed: %s", exc, exc_info=True)
        return None
    if counted is not None:
        logging.info("[social_graph] reaction valence backfill counted=%s", counted)
    return counted


async def relationship_snapshot_loop() -> None:
    """Snapshots are upserted per day, so a few runs a day are idempotent."""
    await run_reaction_valence_backfill()
    while True:
        try:
            saved = await capture_relationship_snapshots()
            logging.info("[social_graph] relationship snapshots captured pairs=%s", saved)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.error("[social_graph] relationship snapshot loop error: %s", exc, exc_info=True)
        await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
