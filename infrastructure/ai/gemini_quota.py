"""Process-wide registry of Gemini key/model pairs with an exhausted daily quota.

Free-tier quotas are per Google Cloud project *and* per model
(``GenerateRequestsPerDayPerProjectPerModel-FreeTier``) and reset at midnight
Pacific time. A short 429 circuit keeps re-trying such a pair every 30-45 s and
burns the first attempt of every DnD turn. Here a daily 429 parks only that
exact key+model until the next reset; other models on the same key and the same
model on other keys stay available.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import threading
import time
from zoneinfo import ZoneInfo

_PACIFIC = ZoneInfo("America/Los_Angeles")
_RESET_MARGIN_SECONDS = 60.0

_lock = threading.Lock()
_exhausted_until: dict[tuple[str, str], float] = {}


def _fingerprint(api_key: str) -> str:
    return hashlib.sha256(str(api_key).encode("utf-8")).hexdigest()[:8]


def _model(model_name: str) -> str:
    return str(model_name or "").removeprefix("models/")


def next_daily_reset(now: float | None = None) -> float:
    """Unix time of the next midnight in America/Los_Angeles (DST-aware)."""
    current = _dt.datetime.fromtimestamp(time.time() if now is None else now, _PACIFIC)
    tomorrow = (current + _dt.timedelta(days=1)).date()
    reset = _dt.datetime.combine(tomorrow, _dt.time(0, 0), tzinfo=_PACIFIC)
    return reset.timestamp()


def _error_text(error: BaseException) -> str:
    parts = [str(error)]
    for attr in ("details", "message", "response_json"):
        value = getattr(error, attr, None)
        if value:
            parts.append(str(value))
    return " ".join(parts).casefold()


def is_daily_quota_error(error: BaseException) -> bool:
    """True only for a per-day quota 429, not a per-minute burst limit."""
    text = _error_text(error)
    code = getattr(error, "code", None) or getattr(error, "status_code", None)
    is_429 = code == 429 or "429" in text or "resource_exhausted" in text or "resourceexhausted" in text
    return is_429 and ("perday" in text or "per_day" in text or "requests per day" in text)


def mark_if_daily_quota(api_key: str, model_name: str, error: BaseException, *, now: float | None = None) -> bool:
    if not api_key or not is_daily_quota_error(error):
        return False
    now = time.time() if now is None else now
    until = next_daily_reset(now) + _RESET_MARGIN_SECONDS
    key = (str(api_key), _model(model_name))
    with _lock:
        already = _exhausted_until.get(key, 0.0) > now
        _exhausted_until[key] = until
    if not already:
        logging.warning(
            "Gemini daily quota exhausted key=%s model=%s parked_until=%s",
            _fingerprint(api_key),
            _model(model_name),
            _dt.datetime.fromtimestamp(until, _PACIFIC).astimezone(_dt.timezone.utc).isoformat(timespec="minutes"),
        )
    return True


def is_exhausted(api_key: str, model_name: str, *, now: float | None = None) -> bool:
    key = (str(api_key), _model(model_name))
    now = time.time() if now is None else now
    with _lock:
        until = _exhausted_until.get(key)
        if until is None:
            return False
        if until <= now:
            _exhausted_until.pop(key, None)
            return False
        return True


def snapshot(now: float | None = None) -> dict[str, int]:
    """Count of parked keys per model, for diagnostics."""
    now = time.time() if now is None else now
    counts: dict[str, int] = {}
    with _lock:
        for (_key, model), until in list(_exhausted_until.items()):
            if until > now:
                counts[model] = counts.get(model, 0) + 1
    return counts


def reset() -> None:
    with _lock:
        _exhausted_until.clear()


__all__ = [
    "is_daily_quota_error",
    "is_exhausted",
    "mark_if_daily_quota",
    "next_daily_reset",
    "reset",
    "snapshot",
]
