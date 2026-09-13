"""Application service for automatic long-term chat Chronicle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

from core.history_store import get_history_repository
from core.paths import USER_MESSAGES_LOG_PATH
from features.chronicle import config
from features.chronicle.ai import classify_candidate
from features.chronicle.backfill import process_backfill_batch
from features.chronicle.models import ChronicleCandidate, ChronicleEvent
from features.chronicle.scoring import activity_signal, find_duplicate_or_related, participant_bonus, reaction_score, text_signal
from infrastructure.persistence.sqlite_chronicle_backfill import SQLiteChronicleBackfillStore
from infrastructure.persistence.sqlite_chronicle_candidates import SQLiteChronicleCandidateStore
from infrastructure.persistence.sqlite_chronicle_events import SQLiteChronicleEventStore


_candidate_store: SQLiteChronicleCandidateStore | None = None
_event_store: SQLiteChronicleEventStore | None = None
_backfill_store: SQLiteChronicleBackfillStore | None = None
_backfill_requested: set[int] = set()


def configure_chronicle(candidate_store: SQLiteChronicleCandidateStore, event_store: SQLiteChronicleEventStore, backfill_store: SQLiteChronicleBackfillStore) -> None:
    global _candidate_store, _event_store, _backfill_store
    _candidate_store = candidate_store
    _event_store = event_store
    _backfill_store = backfill_store


def _stores() -> tuple[SQLiteChronicleCandidateStore, SQLiteChronicleEventStore, SQLiteChronicleBackfillStore]:
    if _candidate_store is None or _event_store is None or _backfill_store is None:
        raise RuntimeError("Chronicle stores are not configured")
    return _candidate_store, _event_store, _backfill_store


def init_db() -> None:
    candidate_store, event_store, backfill_store = _stores()
    candidate_store.init_schema()
    event_store.init_schema()
    backfill_store.init_schema()


def _is_group(chat_type) -> bool:
    return getattr(chat_type, "value", chat_type) in {"group", "supergroup"}


def _person(user) -> dict:
    return {"id": int(user.id), "name": getattr(user, "full_name", None) or getattr(user, "first_name", None) or "Участник", "username": getattr(user, "username", None)}


def _root_reply(message: Message):
    current = getattr(message, "reply_to_message", None)
    if current is None:
        return None
    seen = set()
    for _ in range(4):
        message_id = getattr(current, "message_id", None)
        if message_id in seen:
            break
        seen.add(message_id)
        parent = getattr(current, "reply_to_message", None)
        if parent is None:
            break
        current = parent
    return current


def _ensure_backfill(chat_id: int) -> None:
    if not config.CHRONICLE_BACKFILL_ENABLED or chat_id in _backfill_requested:
        return
    _backfill_requested.add(chat_id)
    _stores()[2].request(chat_id)


async def capture_message(message: Message) -> int | None:
    if not config.CHRONICLE_ENABLED or not _is_group(message.chat.type):
        return None
    if not message.from_user or message.from_user.is_bot:
        return None
    chat_id = int(message.chat.id)
    await asyncio.to_thread(_ensure_backfill, chat_id)
    text = (message.text or message.caption or "").strip()
    if not text or text.startswith("/") or text.casefold().startswith("летопись"):
        return None
    when = message.date or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    burst, velocity, recent_people = activity_signal(chat_id, message.from_user.id, when)
    content = text_signal(text)
    replied = _root_reply(message)
    people = [_person(message.from_user)]
    source_ids = [message.message_id]
    reply_delta = 0
    anchor_message_id = message.message_id
    anchor_text = text
    anchor_user_id = message.from_user.id
    anchor_name = message.from_user.full_name or message.from_user.first_name or "Участник"
    anchor_username = message.from_user.username
    score_delta = content
    if replied is not None:
        anchor_message_id = replied.message_id
        anchor_text = (replied.text or replied.caption or "").strip()
        target = getattr(replied, "from_user", None)
        if target and not target.is_bot:
            people.append(_person(target))
            anchor_user_id = target.id
            anchor_name = target.full_name or target.first_name or "Участник"
            anchor_username = target.username
        source_ids.insert(0, replied.message_id)
        reply_delta = 1
        score_delta += config.REPLY_WEIGHT + participant_bonus(len({p["id"] for p in people})) + burst
    elif content < 1.5:
        return None
    else:
        score_delta += burst
    candidate_id = await asyncio.to_thread(
        _stores()[0].upsert_candidate,
        chat_id=chat_id,
        candidate_key=f"m:{anchor_message_id}",
        timestamp=when,
        due_at=when + timedelta(seconds=config.OBSERVATION_WINDOW_SECONDS),
        score_delta=score_delta,
        anchor_message_id=anchor_message_id,
        anchor_text=anchor_text,
        anchor_user_id=anchor_user_id,
        anchor_display_name=anchor_name,
        anchor_username=anchor_username,
        reply_delta=reply_delta,
        participants=people,
        source_message_ids=source_ids,
        source="live",
        metadata={"velocity_3m": velocity, "participants_3m": recent_people},
    )
    candidate = await asyncio.to_thread(_stores()[0].get_candidate, candidate_id)
    if candidate and candidate.score >= config.CANDIDATE_THRESHOLD:
        logging.info("[chronicle] candidate detected chat=%s id=%s score=%.2f replies=%s", chat_id, candidate.id, candidate.score, candidate.reply_count)
    return candidate_id


def _reaction_name(item: Any) -> str:
    emoji = getattr(item, "emoji", None)
    if emoji:
        return str(emoji)
    custom = getattr(item, "custom_emoji_id", None)
    if custom:
        return f"custom:{custom}"
    return str(getattr(item, "type", None) or item)


async def capture_reaction(update) -> None:
    if not config.CHRONICLE_ENABLED or not _is_group(update.chat.type):
        return
    actor = getattr(update, "user", None)
    if actor is None or actor.is_bot:
        return
    when = update.date or datetime.now(timezone.utc)
    reactions = [_reaction_name(item) for item in (update.new_reaction or [])]
    store = _stores()[0]
    total, unique = await asyncio.to_thread(store.record_user_reaction, update.chat.id, update.message_id, actor.id, reactions, when)
    median, p90 = await asyncio.to_thread(store.reaction_baseline, update.chat.id)
    score = reaction_score(total, unique, median, p90)
    await asyncio.to_thread(
        store.upsert_candidate,
        chat_id=update.chat.id,
        candidate_key=f"m:{update.message_id}",
        timestamp=when,
        due_at=when + timedelta(seconds=config.OBSERVATION_WINDOW_SECONDS),
        score_floor=score,
        anchor_message_id=update.message_id,
        reaction_count=total,
        unique_reactors=unique,
        participants=[_person(actor)],
        source_message_ids=[update.message_id],
        source="live",
        metadata={"reaction_median": median, "reaction_p90": p90},
    )
    logging.info("[chronicle] reaction update chat=%s message=%s reactions=%s unique=%s score=%.2f", update.chat.id, update.message_id, total, unique, score)


async def capture_reaction_count(update) -> None:
    if not config.CHRONICLE_ENABLED or not _is_group(update.chat.type):
        return
    when = update.date or datetime.now(timezone.utc)
    counts = list(getattr(update, "reactions", ()) or ())
    total = sum(max(0, int(getattr(item, "total_count", 0))) for item in counts)
    store = _stores()[0]
    total, unique = await asyncio.to_thread(store.record_aggregate_reaction, update.chat.id, update.message_id, total, sum(1 for item in counts if int(getattr(item, "total_count", 0)) > 0), when)
    median, p90 = await asyncio.to_thread(store.reaction_baseline, update.chat.id)
    score = reaction_score(total, unique, median, p90)
    await asyncio.to_thread(
        store.upsert_candidate,
        chat_id=update.chat.id,
        candidate_key=f"m:{update.message_id}",
        timestamp=when,
        due_at=when + timedelta(seconds=config.OBSERVATION_WINDOW_SECONDS),
        score_floor=score,
        anchor_message_id=update.message_id,
        reaction_count=total,
        unique_reactors=unique,
        source_message_ids=[update.message_id],
        source="live",
        metadata={"reaction_median": median, "reaction_p90": p90},
    )


async def register_external_candidate(*, chat_id: int, context: str, participants: list[dict] | None = None, source_message_ids: list[int] | None = None, source: str, base_score: float | None = None, anchor_message_id: int | None = None) -> int | None:
    """Universal entry point for games/world/radio/etc. without Telegram coupling."""
    if not config.CHRONICLE_ENABLED:
        return None
    digest = hashlib.sha1(f"{source}:{chat_id}:{context}".encode("utf-8")).hexdigest()[:16]
    now = datetime.now(timezone.utc)
    return await asyncio.to_thread(
        _stores()[0].upsert_candidate,
        chat_id=chat_id,
        candidate_key=f"external:{source}:{digest}",
        timestamp=now,
        due_at=now,
        score_floor=base_score if base_score is not None else config.SAVE_THRESHOLD,
        anchor_message_id=anchor_message_id,
        anchor_text=context[:1600],
        participants=participants or [],
        source_message_ids=source_message_ids or [],
        source=source,
        metadata={"external": True},
    )


def _load_context(candidate: ChronicleCandidate) -> list[dict]:
    history = get_history_repository(USER_MESSAGES_LOG_PATH)
    if history is None:
        return []
    padding = timedelta(minutes=config.CONTEXT_PADDING_MINUTES)
    return history.select(candidate.chat_id, start=candidate.started_at - padding, end=candidate.last_activity_at + padding, nonempty=True, limit=config.MAX_CONTEXT_MESSAGES)


def _augment_people(candidate: ChronicleCandidate, context: list[dict]) -> None:
    people: dict[int, dict] = {int(item["id"]): item for item in candidate.metadata.get("participants", []) if isinstance(item, dict) and item.get("id") is not None}
    for row in context:
        try:
            user_id = int(row["user_id"])
        except (KeyError, TypeError, ValueError):
            continue
        people[user_id] = {"id": user_id, "name": row.get("full_name") or row.get("username") or "Участник", "username": row.get("username")}
    candidate.metadata["participants"] = list(people.values())
    candidate.participant_ids = list(people)
    candidate.participant_count = len(people)


async def _reschedule_after_ai_error(candidate: ChronicleCandidate) -> None:
    now = datetime.now(timezone.utc)
    await asyncio.to_thread(_stores()[0].upsert_candidate, chat_id=candidate.chat_id, candidate_key=candidate.candidate_key, timestamp=now, due_at=now + timedelta(minutes=5), score_floor=candidate.score, anchor_message_id=candidate.anchor_message_id, source=candidate.source)


async def finalize_candidate(candidate: ChronicleCandidate) -> str | None:
    candidate_store, event_store, backfill_store = _stores()
    if candidate.score < config.CANDIDATE_THRESHOLD:
        await asyncio.to_thread(candidate_store.mark_candidate, candidate.id, "rejected", "below_candidate_threshold")
        logging.info("[chronicle] rejected id=%s reason=below_candidate_threshold score=%.2f", candidate.id, candidate.score)
        return None
    now = datetime.now(timezone.utc)
    since_day = now - timedelta(hours=24)
    is_backfill = candidate.source == "backfill"
    if not is_backfill:
        event_count = await asyncio.to_thread(event_store.count_since, candidate.chat_id, since_day)
        if event_count >= config.MAX_EVENTS_PER_24H and candidate.score < config.SAVE_THRESHOLD + 2:
            await asyncio.to_thread(candidate_store.mark_candidate, candidate.id, "rejected", "daily_cap")
            return None
        latest = await asyncio.to_thread(event_store.latest_created_at, candidate.chat_id)
        if latest and now - latest < timedelta(seconds=config.COOLDOWN_SECONDS) and candidate.score < config.SAVE_THRESHOLD + 2:
            await asyncio.to_thread(candidate_store.mark_candidate, candidate.id, "rejected", "cooldown")
            return None
    context = await asyncio.to_thread(_load_context, candidate)
    _augment_people(candidate, context)
    candidate_store.metric("chronicle_ai_requests_total")
    if is_backfill:
        await asyncio.to_thread(backfill_store.update, candidate.chat_id, ai_requests_delta=1)
    decision = await classify_candidate(candidate, context)
    if decision.reason == "ai_error":
        await _reschedule_after_ai_error(candidate)
        return None
    if not decision.accepted or decision.draft is None:
        await asyncio.to_thread(candidate_store.mark_candidate, candidate.id, "rejected", decision.reason)
        logging.info("[chronicle] rejected id=%s reason=%s", candidate.id, decision.reason)
        return None
    if decision.raw_confidence < config.AI_CONFIDENCE_THRESHOLD:
        await asyncio.to_thread(candidate_store.mark_candidate, candidate.id, "rejected", "low_ai_confidence")
        return None
    importance = candidate.score + decision.raw_confidence * 2.0
    if importance < config.SAVE_THRESHOLD:
        await asyncio.to_thread(candidate_store.mark_candidate, candidate.id, "rejected", "below_save_threshold")
        return None
    recent = await asyncio.to_thread(event_store.recent, candidate.chat_id, days=45, limit=60)
    duplicate, related = find_duplicate_or_related(decision.draft.title, decision.draft.summary, decision.draft.participant_ids, recent)
    if duplicate is not None:
        candidate_store.metric("chronicle_duplicate_total")
        await asyncio.to_thread(candidate_store.mark_candidate, candidate.id, "rejected", f"duplicate:{duplicate.id}")
        return None
    selected = set(decision.draft.participant_ids)
    people = [item for item in candidate.metadata.get("participants", []) if isinstance(item, dict) and int(item.get("id", -1)) in selected]
    if not people:
        people = list(candidate.metadata.get("participants", []))
    if not is_backfill and len(selected) == 1 and selected:
        sole = next(iter(selected))
        count = await asyncio.to_thread(event_store.participant_count_since, candidate.chat_id, sole, since_day)
        if count >= config.MAX_EVENTS_PER_USER_24H and importance < config.SAVE_THRESHOLD + 2:
            await asyncio.to_thread(candidate_store.mark_candidate, candidate.id, "rejected", "participant_cap")
            return None
    ai_metadata = dict(decision.draft.ai_metadata)
    ai_metadata.update({"confidence": decision.raw_confidence, "heuristic_score": candidate.score, "source": candidate.source})
    event_id = await asyncio.to_thread(
        event_store.save,
        chat_id=candidate.chat_id,
        started_at=candidate.started_at,
        ended_at=candidate.last_activity_at,
        title=decision.draft.title,
        summary=decision.draft.summary,
        category=decision.draft.category,
        importance_score=importance,
        anchor_message_id=candidate.anchor_message_id,
        reaction_count=candidate.reaction_count,
        unique_reactors=candidate.unique_reactors,
        reply_count=candidate.reply_count,
        keywords=decision.draft.keywords,
        entities=decision.draft.entities,
        related_event_ids=related,
        ai_metadata=ai_metadata,
        source=candidate.source,
        participants=people,
        source_message_ids=candidate.source_message_ids,
    )
    await asyncio.to_thread(candidate_store.mark_candidate, candidate.id, "saved", event_id)
    logging.info("[chronicle] saved event=%s chat=%s candidate=%s score=%.2f source=%s", event_id, candidate.chat_id, candidate.id, importance, candidate.source)
    return event_id


async def request_backfill(chat_id: int) -> None:
    if config.CHRONICLE_BACKFILL_ENABLED:
        await asyncio.to_thread(_stores()[2].request, int(chat_id))


async def list_events(chat_id: int, *, user_id: int | None = None, username: str | None = None, limit: int | None = None) -> list[ChronicleEvent]:
    return await asyncio.to_thread(_stores()[1].list, int(chat_id), limit=limit or config.DISPLAY_EVENT_LIMIT, user_id=user_id, username=username)


async def chronicle_loop() -> None:
    while True:
        try:
            if config.CHRONICLE_ENABLED:
                candidate_store, _event_store, backfill_store = _stores()
                cutoff = datetime.now(timezone.utc) - timedelta(hours=config.CANDIDATE_TTL_HOURS)
                await asyncio.to_thread(candidate_store.expire, cutoff)
                due = await asyncio.to_thread(candidate_store.due_candidates, datetime.now(timezone.utc), 3)
                for candidate in due:
                    await finalize_candidate(candidate)
                if config.CHRONICLE_BACKFILL_ENABLED:
                    await process_backfill_batch(candidate_store=candidate_store, backfill_store=backfill_store)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.error("[chronicle] background loop error: %s", exc, exc_info=True)
        await asyncio.sleep(config.FINALIZER_INTERVAL_SECONDS)


class ChronicleCaptureMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data):
        try:
            await capture_message(event)
        except Exception as exc:
            logging.error("[chronicle] message capture failed: %s", exc, exc_info=True)
        return await handler(event, data)
