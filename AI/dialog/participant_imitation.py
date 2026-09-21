"""Fast bounded participant history, profile refresh and semantic-memory helpers."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from AI.dialog.style import create_user_style_prompt, is_participant_style_message
from core.paths import USER_MESSAGES_LOG_PATH as LOG_FILE
from core.history_store import get_history_repository
from services.smart_search import find_relevant_context


PARTICIPANT_TURN_SAMPLE_SIZE = 200
PARTICIPANT_TURN_RECENT_SIZE = 100
PARTICIPANT_INTERACTION_SAMPLE_SIZE = 48
PARTICIPANT_INTERACTION_RECENT_SIZE = 24
PARTICIPANT_RECURRING_TRACK_LIMIT = 512
PARTICIPANT_RECURRING_MAX_ITEMS = 10
PARTICIPANT_HISTORY_CACHE_MAX_ENTRIES = 16
PARTICIPANT_COLD_CACHE_WAIT_SECONDS = 0.75
SEMANTIC_MEMORY_TIMEOUT_SECONDS = 5.0
PROFILE_REFRESH_MESSAGE_DELTA = 50
PROFILE_REFRESH_MAX_AGE = timedelta(days=3)
STYLE_PROFILE_VERSION = 2


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
    interactions: list[str] = field(default_factory=list)
    recurring_counts: Counter[str] = field(default_factory=Counter, repr=False)

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

        normalized_short = re.sub(r"\s+", " ", stripped.casefold())
        word_count = len(re.findall(r"[A-Za-zА-Яа-яЁё0-9_]+", normalized_short))
        if (
            2 <= word_count <= 8
            and len(normalized_short) <= 120
            and not normalized_short.startswith("/")
        ):
            self.recurring_counts[normalized_short] += 1
            if len(self.recurring_counts) > PARTICIPANT_RECURRING_TRACK_LIMIT * 2:
                self.recurring_counts = Counter(
                    dict(self.recurring_counts.most_common(PARTICIPANT_RECURRING_TRACK_LIMIT))
                )

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

    def recurring_messages(self) -> list[str]:
        """Return bounded repeated short utterances mined across the full scan."""
        return [
            message
            for message, count in self.recurring_counts.most_common(PARTICIPANT_RECURRING_MAX_ITEMS)
            if count >= 2
        ]


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


def _compact_interaction_text(text: str, limit: int = 240) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _sample_participant_interactions_sync(
    user_id: int,
    chat_id: int | str,
    *,
    sample_size: int = PARTICIPANT_INTERACTION_SAMPLE_SIZE,
    recent_size: int = PARTICIPANT_INTERACTION_RECENT_SIZE,
) -> list[str]:
    """Sample real preceding-message -> participant-response pairs from indexed history."""
    repository = get_history_repository(LOG_FILE)
    if repository is None or sample_size <= 0:
        return []

    recent_size = min(max(recent_size, 0), sample_size)
    historical_capacity = sample_size - recent_size
    recent: deque[tuple[int, int, str]] = deque(maxlen=recent_size or None)
    historical: list[tuple[int, int, int, str]] = []
    sequence = 0

    def add_historical(item: tuple[int, int, str]) -> None:
        if historical_capacity <= 0:
            return
        item_sequence, row_id, response = item
        digest = hashlib.blake2b(
            f"{chat_id}:{user_id}:{item_sequence}\0{response}".encode("utf-8"),
            digest_size=8,
        ).digest()
        candidate = (int.from_bytes(digest, "big"), item_sequence, row_id, response)
        if len(historical) < historical_capacity:
            historical.append(candidate)
            return
        worst_index = max(range(len(historical)), key=lambda index: historical[index][0])
        if candidate[0] < historical[worst_index][0]:
            historical[worst_index] = candidate

    def visit(row: dict) -> None:
        nonlocal sequence
        response = (row.get("text") or "").strip()
        if not is_participant_style_message(response):
            return
        sequence += 1
        item = (sequence, int(row["id"]), response)
        if recent_size:
            if len(recent) == recent_size:
                add_historical(recent[0])
            recent.append(item)
        else:
            add_historical(item)

    repository.scan(chat_id, visit, user_id=user_id)

    selected = [
        (item_sequence, row_id, response)
        for _score, item_sequence, row_id, response in sorted(historical, key=lambda item: item[1])
    ]
    selected.extend(recent)

    interactions: list[str] = []
    for _item_sequence, row_id, response in selected:
        context_rows = repository.context(chat_id, row_id, radius=1)
        previous_rows = [row for row in context_rows if int(row["id"]) < row_id]
        if not previous_rows:
            continue
        previous = max(previous_rows, key=lambda row: int(row["id"]))
        try:
            previous_user_id = int(previous["user_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if previous_user_id == int(user_id):
            continue

        trigger = _compact_interaction_text(previous.get("text") or "")
        answer = _compact_interaction_text(response)
        if not is_participant_style_message(trigger) or not answer:
            continue
        interactions.append(
            f"Реплика собеседника: {trigger}\n"
            f"Ответ участника: {answer}"
        )

    return interactions


def _resolve_participant_identity_sync(query: str, chat_id: int | str) -> dict | None:
    """Resolve identity with one fast buffered file pass outside the event loop."""
    target = query.strip().lstrip("@").casefold()
    if not target:
        return None

    repository = get_history_repository(LOG_FILE)
    if repository is not None:
        hits = repository.participants(chat_id, **({"user_id": target} if target.isdigit() else {"username": target}))
        if not hits:
            hits = repository.participants(chat_id, full_name=target)
        if not hits:
            return None
        best = max(hits, key=lambda row: (row["message_count"], row["id"]))
        username = _normalize_optional(best["username"], "NoUsername")
        name = _normalize_optional(best["full_name"], "NoName")
        return {"user_id": int(best["user_id"]), "username": username, "full_name": name,
                "display_name": name or username or best["user_id"]}

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
    repository = get_history_repository(LOG_FILE)
    if repository is not None:
        repository.scan(chat_id, lambda row: entry.add_logged_message(row["text"]), user_id=user_id)
        return entry
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
        entry.interactions = await asyncio.to_thread(
            _sample_participant_interactions_sync,
            user_id,
            chat_id,
        )
        _cache_put(key, entry)
        logging.info(
            "Participant history warmup chat=%s user_id=%s messages=%s sample=%s interactions=%s elapsed=%.3fs",
            chat_id,
            user_id,
            entry.message_count,
            len(entry.snapshot()[0]),
            len(entry.interactions),
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
    if settings.get("style_profile_version") != STYLE_PROFILE_VERSION:
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


def refresh_style_profile(
    settings: dict,
    messages: list[str],
    message_count: int,
    interaction_examples: list[str] | None = None,
    recurring_examples: list[str] | None = None,
) -> bool:
    """Refresh the cached prompt when the participant has materially evolved."""
    identity = settings.get("imitated_user", {})
    display_name = identity.get("display_name") or settings.get("prompt_name") or "участник"
    usable = [message for message in messages if is_participant_style_message(message)]
    if not usable or not _profile_needs_refresh(settings, message_count):
        return False

    settings["prompt"] = create_user_style_prompt(
        usable,
        display_name,
        interaction_examples=interaction_examples,
        recurring_examples=recurring_examples,
    )
    settings["style_profile_message_count"] = message_count
    settings["style_profile_updated_at"] = datetime.now(timezone.utc).isoformat()
    settings["style_profile_version"] = STYLE_PROFILE_VERSION
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

    entry, _cache_state = await _get_or_build_history(
        identity["user_id"],
        chat_id,
        sample_size=PARTICIPANT_TURN_SAMPLE_SIZE,
        recent_size=PARTICIPANT_TURN_RECENT_SIZE,
    )
    if entry is None:
        return None
    messages, message_count = entry.snapshot()
    usable = [message for message in messages if is_participant_style_message(message)]
    if not usable:
        return None

    settings["prompt"] = create_user_style_prompt(
        usable,
        identity["display_name"],
        interaction_examples=entry.interactions,
        recurring_examples=entry.recurring_messages(),
    )
    settings["prompt_name"] = identity["display_name"]
    settings["prompt_source"] = "user_imitation"
    settings["prompt_type"] = "user_style"
    settings["imitated_user"] = identity
    settings["style_profile_message_count"] = message_count
    settings["style_profile_updated_at"] = datetime.now(timezone.utc).isoformat()
    settings["style_profile_version"] = STYLE_PROFILE_VERSION
    return identity


def _style_only_memory(reason: str) -> str:
    return (
        "\n\n[SEMANTIC MEMORY]\n"
        f"{reason} Сохраняй манеру речи, но не выдумывай реальные взгляды, биографию, предпочтения или опыт."
        "\n[/SEMANTIC MEMORY]"
    )


async def prepare_participant_turn(
    chat_id: int | str,
    settings: dict,
    query_text: str,
    *,
    current_message_text: str | None = None,
) -> tuple[str, bool]:
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
    if refresh_style_profile(
        settings,
        messages,
        message_count,
        entry.interactions,
        entry.recurring_messages(),
    ):
        changed = True

    # If the imitated participant is also asking the current question, do not
    # feed that just-written message back as supposedly historical evidence.
    query_stripped = (current_message_text if current_message_text is not None else query_text).strip()
    semantic_messages = list(messages)
    if query_stripped and semantic_messages and semantic_messages[-1].strip() == query_stripped:
        semantic_messages.pop()

    semantic_candidates = list(semantic_messages)
    semantic_candidates.extend(entry.interactions)
    semantic_candidates.extend(
        f"Повторяющаяся реплика участника: {message}"
        for message in entry.recurring_messages()
    )

    semantic_started = time.perf_counter()
    try:
        relevant_messages = await asyncio.wait_for(
            find_relevant_context(query_text, semantic_candidates, top_k=5),
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
        len(semantic_candidates),
        cache_elapsed,
        semantic_elapsed,
        time.perf_counter() - total_started,
    )

    if not relevant_messages:
        return _style_only_memory("По текущей теме нет достаточно близких старых сообщений участника."), changed

    memory_lines = "\n".join(f"- {message}" for message in relevant_messages)
    return (
        "\n\n[SEMANTIC MEMORY]\n"
        "Ниже — реальные старые сообщения участника и/или пары «реплика собеседника → ответ участника» на похожую тему. "
        "Используй их как основание не только для содержания, но и для характерной реакции: что человек обычно просит, "
        "отвергает, подкалывает, продолжает или игнорирует. Не копируй целиком уникальные старые реплики; короткие устойчивые "
        "формулировки и повторяющиеся мотивы можно использовать естественно, если они подтверждаются историей.\n"
        f"{memory_lines}\n"
        "[/SEMANTIC MEMORY]",
        changed,
    )
