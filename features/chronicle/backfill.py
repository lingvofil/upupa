"""Two-phase retrospective discovery for Chronicle.

Phase 1 scans the complete managed history locally and builds a bounded working
index of conversational clusters and recurring phrases. Phase 2 ranks those
clusters globally and spends the AI budget only on the strongest, diversified
candidates. The scan itself is never stopped by the AI request limit.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import re

from core.history_store import get_history_repository
from core.paths import USER_MESSAGES_LOG_PATH
from core.time_utils import parse_history_datetime
from features.chronicle import config
from features.chronicle.scoring import participant_bonus, reaction_score
from infrastructure.persistence.sqlite_chronicle_backfill import SQLiteChronicleBackfillStore
from infrastructure.persistence.sqlite_chronicle_candidates import SQLiteChronicleCandidateStore


_WORD_RE = re.compile(r"[\wёЁ]{3,}", re.UNICODE)
_STOP = {
    "это", "как", "что", "там", "вот", "тут", "так", "для", "она", "они", "его", "еще",
    "ещё", "уже", "был", "была", "будет", "просто", "тоже", "только", "когда", "если",
    "потом", "потому", "очень", "меня", "тебя", "себя", "этот", "эта", "эти", "или",
    "который", "которая", "которые", "чтобы", "надо", "можно", "нужно", "есть", "нет",
}


def _phrases(text: str) -> list[str]:
    """Extract a small deterministic set of meme-like 3/4-grams."""
    words = [word.casefold() for word in _WORD_RE.findall(text or "")]
    result: list[str] = []
    for size in (3, 4):
        for index in range(max(0, len(words) - size + 1)):
            part = words[index:index + size]
            if sum(word in _STOP for word in part) >= size - 1:
                continue
            phrase = " ".join(part)
            if len(phrase) >= 14 and phrase not in result:
                result.append(phrase)
            if len(result) >= 12:
                return result
    return result


def _read_batch(history, chat_id: int, after_id: int) -> list[dict]:
    rows: list[dict] = []

    def visitor(row: dict):
        rows.append(row)
        return len(rows) < config.BACKFILL_BATCH_SIZE

    # No extra time cutoff here. ManagedHistoryRepository has already applied
    # the durable deletion/retention policy; scanning anything outside it would
    # incorrectly resurrect logically deleted history from the recovery journal.
    history.scan(
        chat_id,
        visitor,
        after_id=after_id,
        nonempty=True,
        exclude_commands=True,
    )
    return rows


def _clusters(rows: list[dict]) -> list[list[dict]]:
    result: list[list[dict]] = []
    current: list[dict] = []
    previous: datetime | None = None
    gap = timedelta(seconds=config.BACKFILL_CLUSTER_GAP_SECONDS)
    for row in rows:
        when = parse_history_datetime(row["timestamp"])
        if previous is not None and when - previous > gap and current:
            result.append(current)
            current = []
        current.append(row)
        previous = when
    if current:
        result.append(current)
    return result


def _cluster_phrase_candidates(cluster: list[dict], *, limit: int = 40) -> list[str]:
    counts = Counter()
    for row in cluster:
        counts.update(set(_phrases(row.get("text") or "")))
    ranked = sorted(counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    return [phrase for phrase, _count in ranked[:limit]]


def _cluster_base_score(
    cluster: list[dict],
    reactions: dict[int, int],
    baseline: tuple[float, float],
) -> float:
    users = {int(row["user_id"]) for row in cluster}
    message_count = len(cluster)
    score = min(2.0, message_count / 8.0) + participant_bonus(len(users))
    if message_count >= 12 and len(users) >= 3:
        score += 0.8
    max_reactions = max(
        (reactions.get(int(row["message_id"]), 0) for row in cluster if row.get("message_id") is not None),
        default=0,
    )
    if max_reactions:
        # Historical social data cannot reconstruct unique reactors reliably,
        # so total reactions are used for both conservative inputs as in v1.
        score += reaction_score(max_reactions, max_reactions, *baseline)
    return score


def _cluster_record(
    cluster: list[dict],
    reactions: dict[int, int],
    baseline: tuple[float, float],
) -> dict:
    first, last = cluster[0], cluster[-1]
    people: dict[int, dict] = {}
    for row in cluster:
        user_id = int(row["user_id"])
        people[user_id] = {
            "id": user_id,
            "name": row.get("full_name") or row.get("username") or "Участник",
            "username": row.get("username"),
        }
    source_ids = [int(row["message_id"]) for row in cluster if row.get("message_id") is not None]
    anchor = max(
        cluster,
        key=lambda row: (
            reactions.get(int(row["message_id"]), 0) if row.get("message_id") is not None else 0,
            len((row.get("text") or "").strip()),
        ),
    )
    anchor_message_id = int(anchor["message_id"]) if anchor.get("message_id") is not None else None
    max_reactions = max(
        (reactions.get(int(row["message_id"]), 0) for row in cluster if row.get("message_id") is not None),
        default=0,
    )
    return {
        "cluster_key": f"{first['id']}:{last['id']}",
        "history_first_id": int(first["id"]),
        "history_last_id": int(last["id"]),
        "started_at": parse_history_datetime(first["timestamp"]),
        "ended_at": parse_history_datetime(last["timestamp"]),
        "message_count": len(cluster),
        "participants": list(people.values()),
        "source_message_ids": source_ids,
        "anchor_message_id": anchor_message_id,
        "anchor_text": anchor.get("text") or "",
        "anchor_user_id": int(anchor["user_id"]),
        "anchor_display_name": anchor.get("full_name") or anchor.get("username") or "Участник",
        "anchor_username": anchor.get("username"),
        "reaction_count": max_reactions,
        "base_score": _cluster_base_score(cluster, reactions, baseline),
        "phrases": _cluster_phrase_candidates(cluster),
    }


def _batch_phrase_map(clusters: list[dict]) -> dict[str, tuple[int, datetime, list[int]]]:
    """Count phrase recurrence by conversational cluster, not raw messages."""
    occurrences: Counter[str] = Counter()
    last_seen: dict[str, datetime] = {}
    samples: dict[str, set[int]] = defaultdict(set)
    for cluster in clusters:
        ended_at = cluster["ended_at"]
        source_ids = cluster.get("source_message_ids", [])
        for phrase in cluster.get("phrases", []):
            occurrences[phrase] += 1
            last_seen[phrase] = max(last_seen.get(phrase, ended_at), ended_at)
            samples[phrase].update(source_ids[:3])
    return {
        phrase: (count, last_seen[phrase], sorted(samples[phrase])[-12:])
        for phrase, count in occurrences.items()
    }


async def _scan_one_batch(
    state: dict,
    *,
    candidate_store: SQLiteChronicleCandidateStore,
    backfill_store: SQLiteChronicleBackfillStore,
) -> int:
    chat_id = int(state["chat_id"])
    history = get_history_repository(USER_MESSAGES_LOG_PATH)
    if history is None:
        return 0

    if int(state.get("total_messages") or 0) <= 0:
        total = await asyncio.to_thread(
            history.count,
            chat_id,
            nonempty=True,
            exclude_commands=True,
        )
        await asyncio.to_thread(backfill_store.set_total_messages, chat_id, total)

    rows = await asyncio.to_thread(
        _read_batch,
        history,
        chat_id,
        int(state.get("through_history_id") or 0),
    )
    if not rows:
        await asyncio.to_thread(backfill_store.mark_ranking, chat_id)
        return 0

    message_ids = [int(row["message_id"]) for row in rows if row.get("message_id") is not None]
    reactions = await asyncio.to_thread(candidate_store.historical_reaction_counts, chat_id, message_ids)
    baseline = await asyncio.to_thread(candidate_store.reaction_baseline, chat_id)
    clusters = [_cluster_record(cluster, reactions, baseline) for cluster in _clusters(rows)]
    phrase_map = _batch_phrase_map(clusters)
    await asyncio.to_thread(
        backfill_store.record_scan_batch,
        chat_id,
        through_history_id=int(rows[-1]["id"]),
        scanned_messages=len(rows),
        phrases=phrase_map,
        clusters=clusters,
    )
    return len(rows)


async def _promote_ranked_candidates(
    chat_id: int,
    *,
    candidate_store: SQLiteChronicleCandidateStore,
    backfill_store: SQLiteChronicleBackfillStore,
) -> int:
    ranked = await asyncio.to_thread(
        backfill_store.top_clusters,
        chat_id,
        limit=config.BACKFILL_MAX_AI_REQUESTS,
        min_score=config.BACKFILL_MIN_SCORE,
    )
    now = datetime.now(timezone.utc)
    queued = 0
    for cluster in ranked:
        key = f"backfill:v2:{cluster['cluster_key']}"
        metadata = {
            "recurring_phrases": cluster.get("recurring_phrases", []),
            "history_range": [cluster["history_first_id"], cluster["history_last_id"]],
            "message_count": cluster["message_count"],
            "phrase_total": cluster.get("phrase_total", 0),
            "base_score": cluster["base_score"],
        }
        common = dict(
            chat_id=chat_id,
            candidate_key=key,
            due_at=now,
            score_floor=float(cluster["final_score"]),
            anchor_message_id=cluster.get("anchor_message_id"),
            anchor_text=cluster.get("anchor_text") or "",
            anchor_user_id=cluster.get("anchor_user_id"),
            anchor_display_name=cluster.get("anchor_display_name") or "Участник",
            anchor_username=cluster.get("anchor_username"),
            reaction_count=int(cluster.get("reaction_count") or 0),
            unique_reactors=0,
            participants=cluster.get("participants", []),
            source_message_ids=cluster.get("source_message_ids", []),
            source="backfill",
            metadata=metadata,
        )
        # Two idempotent upserts preserve the full cluster time range: the first
        # creates the earliest started_at, the second advances last_activity_at.
        await asyncio.to_thread(
            candidate_store.upsert_candidate,
            timestamp=parse_history_datetime(cluster["started_at"]),
            **common,
        )
        await asyncio.to_thread(
            candidate_store.upsert_candidate,
            timestamp=parse_history_datetime(cluster["ended_at"]),
            **common,
        )
        queued += 1

    await asyncio.to_thread(backfill_store.set_classifying, chat_id, queued)
    return queued


async def process_backfill_batch(
    *,
    candidate_store: SQLiteChronicleCandidateStore,
    backfill_store: SQLiteChronicleBackfillStore,
) -> int:
    """Advance one chat through scan -> rank -> classify lifecycle.

    Several local batches are scanned per scheduler tick to make initial
    indexing finish in a reasonable time without increasing AI concurrency.
    Return value is the number of history messages scanned during this call.
    """
    state = await asyncio.to_thread(backfill_store.next, config.BACKFILL_MAX_AI_REQUESTS)
    if not state:
        return 0
    chat_id = int(state["chat_id"])
    status = state["status"]

    if status == "classifying":
        await asyncio.to_thread(backfill_store.complete_if_finished, chat_id)
        return 0

    if status == "ranking":
        await _promote_ranked_candidates(
            chat_id,
            candidate_store=candidate_store,
            backfill_store=backfill_store,
        )
        return 0

    scanned = 0
    current = state
    for _ in range(config.BACKFILL_SCAN_BATCHES_PER_TICK):
        amount = await _scan_one_batch(
            current,
            candidate_store=candidate_store,
            backfill_store=backfill_store,
        )
        scanned += amount
        current = await asyncio.to_thread(backfill_store.state, chat_id)
        if not current or current["status"] == "ranking":
            await _promote_ranked_candidates(
                chat_id,
                candidate_store=candidate_store,
                backfill_store=backfill_store,
            )
            break
        if amount <= 0:
            break
    return scanned
