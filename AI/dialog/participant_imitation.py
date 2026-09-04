"""Fast bounded participant history, profile refresh and semantic-memory helpers."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from AI.dialog.style import create_user_style_prompt, is_participant_style_message
from core.paths import USER_MESSAGES_LOG_PATH as LOG_FILE
from services.smart_search import find_relevant_context


PARTICIPANT_TURN_SAMPLE_SIZE = 200
PARTICIPANT_TURN_RECENT_SIZE = 100
PARTICIPANT_HISTORY_CACHE_MAX_ENTRIES = 16
PARTICIPANT_COLD_CACHE_WAIT_SECONDS = 0.75
SEMANTIC_MEMORY_TIMEOUT_SECONDS = 5.0
PROFILE_REFRESH_MESSAGE_DELTA = 50
PROFILE_REFRESH_MAX_AGE = timedelta(days=3)


@dataclass
class ParticipantHistory:
    """Bounded deterministic recent + historical sample for one participant."""

    chat_id: str
    user_id: int
    sample_size: int
    recent_size: int
    message_count: int = 0
    sequence: int = 0
    historical: list[tuple[int, int, str]] = field(default_factory=list)
    recent: deque[tuple[int, str]] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self.recent_size = min(max(self.recent_size, 0), self.sample_size)
        self.recent = deque(self.recent, maxlen=self.recent_size or None)

    @property
    def historical_capacity(self) -> int:
        return max(self.sample_size - self.recent_size, 0)

    def _historical_score(self, sequence: int, text: str) -> int:
        digest = hashlib.blake2b(
            f"{self.chat_id}:{self.user_id}:{sequence}\0{text}".encode("utf-8"),
            digest_size=8,
        ).digest()
        return int.from_bytes(digest, "big")

    def _add_historical(self, item: tuple[int, str]) -> None:
        if self.historical_capacity <= 0:
            return
        sequence, text = item
        candidate = (self._historical_score(sequence, text), sequence, text)
        if len(self.historical) < self.historical_capacity:
            self.historical.append(candidate)
            return

        worst_index = max(range(len(self.historical)), key=lambda index: self.historical[index][0])
        if candidate[0] < self.historical[worst_index][0]:
            self.historical[worst_index] = candidate

    def add_logged_message(self, text: str) -> None:
        """Apply one newly persisted log message without rereading the log."""
        self.message_count += 1
        stripped = (text or "").strip()
        if not stripped:
            return

        self.sequence += 1
        item = (self.sequence, stripped)
        if self.recent_size:
            if len(self.recent) == self.recent_size:
                self._add_historical(self.recent[0])
            self.recent.append(item)
        else:
            self._add_historical(item)

    def snapshot(self) -> tuple[list[str], int]:
        historical = sorted(self.historical, key=lambda item: item[1])
        messages = [text for _score, _sequence, text in historical]
        messages.extend(text for _sequence, text in self.recent)
        return messages, self.message_count


CacheKey = tuple[str, int, int, int]
_PARTICIPANT_HISTORY_CACHE: OrderedDict[CacheKey, ParticipantHistory] = OrderedDict()
_PARTICIPANT_BUILD_TASKS: dict[CacheKey, asyncio.Task] = {}


def _cache_key(
    chat_id: int | str,
    user_id: int,
    sample_size: int,
    recent_size: int,
) -> CacheKey:
    return str(chat_id), int(user_id), int(sample_size), int(recent_size)


def _cache_get(key: CacheKey) -> ParticipantHistory | None:
    entry = _PARTICIPANT_HISTORY_CACHE.get(key)
    if entry is not None:
        _PARTICIPANT_HISTORY_CACHE.move_to_end(key)
    return entry


def _cache_put(key: CacheKey, entry: ParticipantHistory) -> ParticipantHistory:
    _PARTICIPANT_HISTORY_CACHE[key] = entry
    _PARTICIPANT_HISTORY_CACHE.move_to_end(key)
    while len(_PARTICIPANT_HISTORY_CACHE) > PARTICIPANT_HISTORY_CACHE_MAX_ENTRIES:
        _PARTICIPANT_HISTORY_CACHE.popitem(last=False)
    return entry


def clear_participant_history_cache() -> None:
    """Test/maintenance helper; normal production code relies on bounded LRU eviction."""
    _PARTICIPANT_HISTORY_CACHE.clear()
    _PARTICIPANT_BUILD_TASKS.clear()


def _log_pattern(chat_id: int | str) -> re.Pattern:
    return re.compile(
        rf".* - Chat {re.escape(str(chat_id))}\b.*User (?P<user_id>\d+) "
        r"\((?P<username>[^)]+)\) \[(?P<full_name>.+?)\]: (?P<text>.*)"
    )


def _normalize_optional(value: str, sentinel: str) -> str | None:
    value = value.strip()
    return None if not value or value == sentinel else value


def _resolve_participant_identity_sync(query: str, chat_id: int | str) -> dict | None:
    """Resolve identity with one fast buffered file pass outside the event loop."""
    target = query.strip().lstrip("@").casefold()
    if not target:
        return None

    username_hits: dict[int, dict] = {}
    full_name_hits: dict[int, dict] = {}
    sequence = 0
    chat_marker = f" - Chat {chat_id}"
    pattern = _log_pattern(chat_id)

    with open(LOG_FILE, mode="r", encoding="utf-8", buffering=1024 * 1024) as file:
        for line in file:
            if chat_marker not in line:
                continue
            match = pattern.match(line)
            if not match:
                continue
            sequence += 1
            user_id = int(match.group("user_id"))
            username = _normalize_optional(match.group("username"), "NoUsername")
            full_name = _normalize_optional(match.group("full_name"), "NoName")

            bucket = None
            if username and username.casefold() == target:
                bucket = username_hits
            elif full_name and full_name.casefold() == target:
                bucket = full_name_hits
            elif target.isdigit() and int(target) == user_id:
                bucket = username_hits

            if bucket is None:
                continue

            stats = bucket.setdefault(
                user_id,
                {
                    "user_id": user_id,
                    "username": username,
                    "full_name": full_name,
                    "message_count": 0,
                    "last_sequence": sequence,
                },
            )
            stats["username"] = username or stats.get("username")
            stats["full_name"] = full_name or stats.get("full_name")
            stats["message_count"] += 1
            stats["last_sequence"] = sequence

    hits = username_hits or full_name_hits
    if not hits:
        return None

    best = max(hits.values(), key=lambda item: (item["message_count"], item["last_sequence"]))
    return {
        "user_id": best["user_id"],
        "username": best.get("username"),
        "full_name": best.get("full_name"),
        "display_name": best.get("full_name") or best.get("username") or str(best["user_id"]),
    }


async def resolve_participant_identity(query: str, chat_id: int | str) -> dict | None:
    """Resolve a username/full name once and pin imitation to stable Telegram user_id."""
    return await asyncio.to_thread(_resolve_participant_identity_sync, query, chat_id)


def _scan_participant_history_sync(
    user_id: int,
    chat_id: int | str,
    sample_size: int,
    recent_size: int,
) -> ParticipantHistory:
    """Build a bounded cache with one buffered synchronous scan in a worker thread."""
    entry = ParticipantHistory(
        chat_id=str(chat_id),
        user_id=int(user_id),
        sample_size=sample_size,
        recent_size=recent_size,
    )
    chat_marker = f" - Chat {chat_id}"
    user_marker = f"User {int(user_id)} "
    pattern = _log_pattern(chat_id)

    with open(LOG_FILE, mode="r", encoding="utf-8", buffering=1024 * 1024) as file:
        for line in file:
            if chat_marker not in line or user_marker not in line:
                continue
            match = pattern.match(line)
            if not match or int(match.group("user_id")) != int(user_id):
                continue
            entry.add_logged_message(match.group("text"))

    return entry


async def _build_and_store_history(key: CacheKey) -> ParticipantHistory:
    chat_id, user_id, sample_size, recent_size = key
    started = time.perf_counter()
    try:
        entry = await asyncio.to_thread(
            _scan_participant_history_sync,
            user_id,
            chat_id,
            sample_size,
            recent_size,
        )
        _cache_put(key, entry)
        logging.info(
            "Participant history warmup chat=%s user_id=%s messages=%s sample=%s elapsed=%.3fs",
            chat_id,
            user_id,
            entry.message_count,
            len(entry.snapshot()[0]),
            time.perf_counter() - started,
        )
        return entry
    finally:
        _PARTICIPANT_BUILD_TASKS.pop(key, None)


def _ensure_history_build(key: CacheKey) -> asyncio.Task:
    task = _PARTICIPANT_BUILD_TASKS.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_build_and_store_history(key))
        _PARTICIPANT_BUILD_TASKS[key] = task
    return task


async def _get_or_build_history(
    user_id: int,
    chat_id: int | str,
    *,
    sample_size: int,
    recent_size: int,
    max_wait_seconds: float | None = None,
) -> tuple[ParticipantHistory | None, str]:
    key = _cache_key(chat_id, user_id, sample_size, recent_size)
    cached = _cache_get(key)
    if cached is not None:
        return cached, "hit"

    task = _ensure_history_build(key)
    if max_wait_seconds is None:
        return await task, "built"

    try:
        entry = await asyncio.wait_for(asyncio.shield(task), timeout=max_wait_seconds)
        return entry, "built"
    except asyncio.TimeoutError:
        logging.info(
            "Participant history warmup still running chat=%s user_id=%s after %.2fs; answering style-only",
            chat_id,
            user_id,
            max_wait_seconds,
        )
        return None, "warming"


async def sample_participant_messages(
    user_id: int,
    chat_id: int | str,
    *,
    sample_size: int = PARTICIPANT_TURN_SAMPLE_SIZE,
    recent_size: int = PARTICIPANT_TURN_RECENT_SIZE,
) -> tuple[list[str], int]:
    """Return a bounded cached sample, scanning the log only on a cold cache."""
    if sample_size <= 0:
        return [], 0
    entry, _state = await _get_or_build_history(
        user_id,
        chat_id,
        sample_size=sample_size,
        recent_size=recent_size,
    )
    return entry.snapshot() if entry is not None else ([], 0)


def record_participant_message(message) -> None:
    """Incrementally update any warm participant caches after the log write."""
    chat = getattr(message, "chat", None)
    user = getattr(message, "from_user", None)
    if chat is None or user is None:
        return

    chat_id = str(chat.id)
    user_id = int(user.id)
    text = getattr(message, "text", None) or ""

    for key, entry in list(_PARTICIPANT_HISTORY_CACHE.items()):
        if key[0] == chat_id and key[1] == user_id:
            entry.add_logged_message(text)
            _PARTICIPANT_HISTORY_CACHE.move_to_end(key)


def _profile_needs_refresh(settings: dict, message_count: int) -> bool:
    if not settings.get("prompt"):
        return True

    previous_count = settings.get("style_profile_message_count")
    if not isinstance(previous_count, int) or message_count >= previous_count + PROFILE_REFRESH_MESSAGE_DELTA:
        return True

    updated_at = settings.get("style_profile_updated_at")
    if not updated_at:
        return True
    try:
        parsed = datetime.fromisoformat(updated_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True

    return datetime.now(timezone.utc) - parsed >= PROFILE_REFRESH_MAX_AGE


def refresh_style_profile(settings: dict, messages: list[str], message_count: int) -> bool:
    """Refresh the cached prompt when the participant has materially evolved."""
    identity = settings.get("imitated_user", {})
    display_name = identity.get("display_name") or settings.get("prompt_name") or "участник"
    usable = [message for message in messages if is_participant_style_message(message)]
    if not usable or not _profile_needs_refresh(settings, message_count):
        return False

    settings["prompt"] = create_user_style_prompt(usable, display_name)
    settings["style_profile_message_count"] = message_count
    settings["style_profile_updated_at"] = datetime.now(timezone.utc).isoformat()
    return True


async def initialize_participant_profile(
    chat_id: int | str,
    query: str,
    settings: dict,
) -> dict | None:
    """Resolve a requested participant, warm its cache and build the initial profile."""
    identity = await resolve_participant_identity(query, chat_id)
    if not identity:
        return None

    messages, message_count = await sample_participant_messages(identity["user_id"], chat_id)
    usable = [message for message in messages if is_participant_style_message(message)]
    if not usable:
        return None

    settings["prompt"] = create_user_style_prompt(usable, identity["display_name"])
    settings["prompt_name"] = identity["display_name"]
    settings["prompt_source"] = "user_imitation"
    settings["prompt_type"] = "user_style"
    settings["imitated_user"] = identity
    settings["style_profile_message_count"] = message_count
    settings["style_profile_updated_at"] = datetime.now(timezone.utc).isoformat()
    return identity


def _style_only_memory(reason: str) -> str:
    return (
        "\n\n[SEMANTIC MEMORY]\n"
        f"{reason} Сохраняй манеру речи, но не выдумывай реальные взгляды, биографию, предпочтения или опыт."
        "\n[/SEMANTIC MEMORY]"
    )


async def prepare_participant_turn(chat_id: int | str, settings: dict, query_text: str) -> tuple[str, bool]:
    """Return semantic memory without letting cache warmup block a live reply."""
    total_started = time.perf_counter()
    identity = settings.get("imitated_user", {})
    changed = False

    user_id = identity.get("user_id")
    if not user_id:
        legacy_name = identity.get("username") or identity.get("full_name") or identity.get("display_name")
        if legacy_name:
            migration_started = time.perf_counter()
            resolved = await resolve_participant_identity(legacy_name, chat_id)
            logging.info(
                "Participant identity migration chat=%s elapsed=%.3fs",
                chat_id,
                time.perf_counter() - migration_started,
            )
            if resolved:
                settings["imitated_user"] = resolved
                identity = resolved
                user_id = resolved["user_id"]
                changed = True

    if not user_id:
        return _style_only_memory("Нет надёжно идентифицированной памяти участника."), changed

    cache_started = time.perf_counter()
    entry, cache_state = await _get_or_build_history(
        int(user_id),
        chat_id,
        sample_size=PARTICIPANT_TURN_SAMPLE_SIZE,
        recent_size=PARTICIPANT_TURN_RECENT_SIZE,
        max_wait_seconds=PARTICIPANT_COLD_CACHE_WAIT_SECONDS,
    )
    cache_elapsed = time.perf_counter() - cache_started

    if entry is None:
        logging.info(
            "Participant context timing chat=%s user_id=%s cache_state=%s cache=%.3fs semantic=0.000s total=%.3fs",
            chat_id,
            user_id,
            cache_state,
            cache_elapsed,
            time.perf_counter() - total_started,
        )
        return _style_only_memory("История участника ещё прогревается; этот ответ использует только готовый style profile."), changed

    messages, message_count = entry.snapshot()
    if refresh_style_profile(settings, messages, message_count):
        changed = True

    # If the imitated participant is also asking the current question, do not
    # feed that just-written message back as supposedly historical evidence.
    query_stripped = query_text.strip()
    semantic_messages = list(messages)
    if query_stripped and semantic_messages and semantic_messages[-1].strip() == query_stripped:
        semantic_messages.pop()

    semantic_started = time.perf_counter()
    try:
        relevant_messages = await asyncio.wait_for(
            find_relevant_context(query_text, semantic_messages, top_k=3),
            timeout=SEMANTIC_MEMORY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        relevant_messages = []
        logging.warning(
            "Participant semantic memory timed out chat=%s user_id=%s after %.1fs; answering style-only",
            chat_id,
            user_id,
            SEMANTIC_MEMORY_TIMEOUT_SECONDS,
        )
    semantic_elapsed = time.perf_counter() - semantic_started
    logging.info(
        "Participant context timing chat=%s user_id=%s cache_state=%s candidates=%s cache=%.3fs semantic=%.3fs total=%.3fs",
        chat_id,
        user_id,
        cache_state,
        len(semantic_messages),
        cache_elapsed,
        semantic_elapsed,
        time.perf_counter() - total_started,
    )

    if not relevant_messages:
        return _style_only_memory("По текущей теме нет достаточно близких старых сообщений участника."), changed

    memory_lines = "\n".join(f"- {message}" for message in relevant_messages)
    return (
        "\n\n[SEMANTIC MEMORY]\n"
        "Ниже — реальные старые сообщения участника на похожую тему. Используй их только как основание для "
        "его возможной позиции и контекста. Не цитируй их дословно и не копируй характерные фразы механически.\n"
        f"{memory_lines}\n"
        "[/SEMANTIC MEMORY]",
        changed,
    )
