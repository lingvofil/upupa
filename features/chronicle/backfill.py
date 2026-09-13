"""Bounded historical discovery for Chronicle."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import re

from core.history_store import get_history_repository
from core.paths import USER_MESSAGES_LOG_PATH
from features.chronicle import config
from features.chronicle.scoring import participant_bonus, reaction_score
from infrastructure.persistence.sqlite_chronicle_backfill import SQLiteChronicleBackfillStore
from infrastructure.persistence.sqlite_chronicle_candidates import SQLiteChronicleCandidateStore


_WORD_RE = re.compile(r"[\wёЁ]{3,}", re.UNICODE)
_STOP = {
    "это", "как", "что", "там", "вот", "тут", "так", "для", "она", "они", "его", "еще",
    "ещё", "уже", "был", "была", "будет", "просто", "тоже", "только", "когда", "если",
    "потом", "потому", "очень", "меня", "тебя", "себя", "этот", "эта", "эти", "или",
}


def _phrases(text: str) -> list[str]:
    words = [word.casefold() for word in _WORD_RE.findall(text or "")]
    result: list[str] = []
    for size in (3, 4):
        for index in range(max(0, len(words) - size + 1)):
            part = words[index:index + size]
            if sum(word in _STOP for word in part) >= size - 1:
                continue
            phrase = " ".join(part)
            if len(phrase) >= 14:
                result.append(phrase)
            if len(result) >= 20:
                return result
    return result


def _read_batch(history, chat_id: int, after_id: int, cutoff: datetime) -> list[dict]:
    rows: list[dict] = []

    def visitor(row: dict):
        rows.append(row)
        return len(rows) < config.BACKFILL_BATCH_SIZE

    history.scan(
        chat_id,
        visitor,
        start=cutoff,
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
        when = datetime.fromisoformat(row["timestamp"])
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if previous is not None and when - previous > gap and current:
            result.append(current)
            current = []
        current.append(row)
        previous = when
    if current:
        result.append(current)
    return result


def _cluster_phrases(cluster: list[dict], totals: dict[str, int]) -> tuple[int, list[str]]:
    found = Counter()
    for row in cluster:
        found.update(set(_phrases(row.get("text") or "")))
    ranked = sorted(((totals.get(phrase, 0), phrase) for phrase in found), reverse=True)
    best = ranked[0][0] if ranked else 0
    return best, [phrase for count, phrase in ranked[:5] if count >= 2]


def _cluster_score(
    cluster: list[dict],
    phrase_total: int,
    reactions: dict[int, int],
    baseline: tuple[float, float],
) -> float:
    users = {int(row["user_id"]) for row in cluster}
    message_count = len(cluster)
    score = min(2.0, message_count / 8.0) + participant_bonus(len(users))
    if message_count >= 12 and len(users) >= 3:
        score += 0.8
    if phrase_total >= 5:
        score += 3.0
    elif phrase_total >= 3:
        score += 2.0
    elif phrase_total >= 2:
        score += 0.8
    max_reactions = max(
        (reactions.get(int(row["message_id"]), 0) for row in cluster if row.get("message_id") is not None),
        default=0,
    )
    if max_reactions:
        score += reaction_score(max_reactions, max_reactions, *baseline)
    return score


async def process_backfill_batch(
    *,
    candidate_store: SQLiteChronicleCandidateStore,
    backfill_store: SQLiteChronicleBackfillStore,
) -> int:
    state = await asyncio.to_thread(backfill_store.next, config.BACKFILL_MAX_AI_REQUESTS)
    if not state:
        return 0
    chat_id = int(state["chat_id"])
    history = get_history_repository(USER_MESSAGES_LOG_PATH)
    if history is None:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=config.BACKFILL_LOOKBACK_DAYS)
    rows = await asyncio.to_thread(_read_batch, history, chat_id, int(state["through_history_id"]), cutoff)
    if not rows:
        await asyncio.to_thread(backfill_store.update, chat_id, completed=True)
        return 0

    phrase_map: dict[str, tuple[int, datetime, list[int]]] = {}
    by_phrase: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        for phrase in set(_phrases(row.get("text") or "")):
            by_phrase[phrase].append(row)
    for phrase, items in by_phrase.items():
        seen = datetime.fromisoformat(items[-1]["timestamp"])
        message_ids = [item["message_id"] for item in items if item.get("message_id") is not None]
        phrase_map[phrase] = (len(items), seen, message_ids)

    totals = await asyncio.to_thread(backfill_store.record_phrases, chat_id, phrase_map)
    message_ids = [int(row["message_id"]) for row in rows if row.get("message_id") is not None]
    reactions = await asyncio.to_thread(candidate_store.historical_reaction_counts, chat_id, message_ids)
    baseline = await asyncio.to_thread(candidate_store.reaction_baseline, chat_id)

    ranked = []
    for cluster in _clusters(rows):
        phrase_total, recurring = _cluster_phrases(cluster, totals)
        score = _cluster_score(cluster, phrase_total, reactions, baseline)
        if score >= config.BACKFILL_MIN_SCORE:
            ranked.append((score, cluster, recurring))
    ranked.sort(key=lambda item: item[0], reverse=True)

    queued = 0
    for score, cluster, recurring in ranked[:config.BACKFILL_MAX_CANDIDATES_PER_BATCH]:
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
            key=lambda row: reactions.get(int(row["message_id"]), 0) if row.get("message_id") is not None else 0,
        )
        anchor_message_id = int(anchor["message_id"]) if anchor.get("message_id") is not None else None
        when = datetime.fromisoformat(last["timestamp"])
        await asyncio.to_thread(
            candidate_store.upsert_candidate,
            chat_id=chat_id,
            candidate_key=f"backfill:{first['id']}:{last['id']}",
            timestamp=when,
            due_at=datetime.now(timezone.utc),
            score_floor=score,
            anchor_message_id=anchor_message_id,
            anchor_text=anchor.get("text") or "",
            anchor_user_id=int(anchor["user_id"]),
            anchor_display_name=anchor.get("full_name") or anchor.get("username") or "Участник",
            anchor_username=anchor.get("username"),
            reaction_count=reactions.get(anchor_message_id, 0) if anchor_message_id is not None else 0,
            unique_reactors=0,
            participants=list(people.values()),
            source_message_ids=source_ids,
            source="backfill",
            metadata={"recurring_phrases": recurring, "history_range": [first["id"], last["id"]]},
        )
        queued += 1

    await asyncio.to_thread(
        backfill_store.update,
        chat_id,
        through_history_id=int(rows[-1]["id"]),
        candidates_delta=len(ranked),
        status="running",
    )
    return queued
