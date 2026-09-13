"""Runtime configuration for the long-term chat Chronicle."""

from __future__ import annotations

import os


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


# Support the short flag from the feature brief as well as the project's usual
# UPUPA_* namespace. The feature is enabled by default after deployment.
CHRONICLE_ENABLED = _bool(
    "UPUPA_CHRONICLE_ENABLED",
    _bool("CHRONICLE_ENABLED", True),
)
CHRONICLE_BACKFILL_ENABLED = _bool("UPUPA_CHRONICLE_BACKFILL_ENABLED", True)

CANDIDATE_THRESHOLD = _float("UPUPA_CHRONICLE_CANDIDATE_THRESHOLD", 2.5)
SAVE_THRESHOLD = _float("UPUPA_CHRONICLE_SAVE_THRESHOLD", 5.0)
AI_CONFIDENCE_THRESHOLD = _float("UPUPA_CHRONICLE_AI_CONFIDENCE_THRESHOLD", 0.65)

OBSERVATION_WINDOW_SECONDS = _int("UPUPA_CHRONICLE_OBSERVATION_WINDOW_SECONDS", 300, minimum=30)
CANDIDATE_TTL_HOURS = _int("UPUPA_CHRONICLE_CANDIDATE_TTL_HOURS", 48, minimum=1)
FINALIZER_INTERVAL_SECONDS = _int("UPUPA_CHRONICLE_FINALIZER_INTERVAL_SECONDS", 20, minimum=5)
COOLDOWN_SECONDS = _int("UPUPA_CHRONICLE_COOLDOWN_SECONDS", 1800, minimum=0)
MAX_CONTEXT_MESSAGES = _int("UPUPA_CHRONICLE_MAX_CONTEXT_MESSAGES", 40, minimum=8)
CONTEXT_PADDING_MINUTES = _int("UPUPA_CHRONICLE_CONTEXT_PADDING_MINUTES", 12, minimum=2)

REACTION_WEIGHT = _float("UPUPA_CHRONICLE_REACTION_WEIGHT", 0.8)
UNIQUE_REACTOR_WEIGHT = _float("UPUPA_CHRONICLE_UNIQUE_REACTOR_WEIGHT", 0.45)
REPLY_WEIGHT = _float("UPUPA_CHRONICLE_REPLY_WEIGHT", 1.35)
PARTICIPANT_WEIGHT = _float("UPUPA_CHRONICLE_PARTICIPANT_WEIGHT", 0.55)
BURST_WEIGHT = _float("UPUPA_CHRONICLE_BURST_WEIGHT", 0.7)
EMOJI_TEXT_WEIGHT = _float("UPUPA_CHRONICLE_EMOJI_TEXT_WEIGHT", 0.08)

MAX_EVENTS_PER_24H = _int("UPUPA_CHRONICLE_MAX_EVENTS_PER_24H", 6, minimum=1)
MAX_EVENTS_PER_USER_24H = _int("UPUPA_CHRONICLE_MAX_EVENTS_PER_USER_24H", 3, minimum=1)
DISPLAY_EVENT_LIMIT = _int("UPUPA_CHRONICLE_DISPLAY_EVENT_LIMIT", 8, minimum=1)

BACKFILL_BATCH_SIZE = _int("UPUPA_CHRONICLE_BACKFILL_BATCH_SIZE", 500, minimum=50)
BACKFILL_LOOKBACK_DAYS = _int("UPUPA_CHRONICLE_BACKFILL_LOOKBACK_DAYS", 3650, minimum=1)
BACKFILL_MAX_AI_REQUESTS = _int("UPUPA_CHRONICLE_BACKFILL_MAX_AI_REQUESTS", 20, minimum=0)
BACKFILL_MAX_CANDIDATES_PER_BATCH = _int(
    "UPUPA_CHRONICLE_BACKFILL_MAX_CANDIDATES_PER_BATCH", 2, minimum=1
)
BACKFILL_MIN_SCORE = _float("UPUPA_CHRONICLE_BACKFILL_MIN_SCORE", 4.5)
BACKFILL_CLUSTER_GAP_SECONDS = _int("UPUPA_CHRONICLE_BACKFILL_CLUSTER_GAP_SECONDS", 480, minimum=60)
