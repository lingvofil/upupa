"""Единая временная зона процесса и истории Упупы.

Исторически планировщики и пользовательские функции бота работают по Москве.
Фиксируем это явно, чтобы поведение не зависело от timezone Linux/VPS.
"""

from __future__ import annotations

from datetime import datetime
import os
import time

import pytz

from core.settings import APP_TIMEZONE_NAME


APP_TIMEZONE = pytz.timezone(APP_TIMEZONE_NAME)


def configure_process_timezone() -> None:
    """Configure libc/Python local time on platforms that support ``tzset``."""
    os.environ["TZ"] = APP_TIMEZONE_NAME
    tzset = getattr(time, "tzset", None)
    if tzset is not None:
        tzset()


def as_app_datetime(value: datetime) -> datetime:
    """Return an aware datetime in the configured application timezone.

    Naive values are legacy application wall-clock timestamps and are therefore
    interpreted in APP_TIMEZONE rather than in the host machine timezone.
    """
    if value.tzinfo is None:
        return APP_TIMEZONE.localize(value)
    return value.astimezone(APP_TIMEZONE)


def app_now() -> datetime:
    return datetime.now(APP_TIMEZONE)


def app_now_naive() -> datetime:
    """Wall-clock value compatible with the legacy SQLite/journal format."""
    return app_now().replace(tzinfo=None)


def history_timestamp(value: datetime) -> str:
    """Normalize a datetime to the legacy sortable app-wall-clock representation."""
    return as_app_datetime(value).replace(tzinfo=None).isoformat(timespec="microseconds")


def parse_history_datetime(value: str | datetime) -> datetime:
    """Parse a history/admin timestamp and return aware application time."""
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return as_app_datetime(parsed)
