"""Server-side authentication helpers for Telegram Mini Apps."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import time
from typing import Mapping
from urllib.parse import parse_qsl


DEFAULT_MAX_AGE_SECONDS = 24 * 60 * 60
CLOCK_SKEW_SECONDS = 60
MAX_INIT_DATA_LENGTH = 16 * 1024
_ROOM_RE = re.compile(r"^(?:m\d+|-?\d+)$")


class WebAppAuthError(ValueError):
    """Raised when Telegram Mini App init data cannot be trusted."""


@dataclass(frozen=True)
class TelegramWebAppIdentity:
    user_id: int
    auth_date: int
    start_param: str | None = None


def validate_telegram_init_data(
    init_data: str,
    bot_token: str,
    *,
    now: float | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> TelegramWebAppIdentity:
    """Validate ``Telegram.WebApp.initData`` using Telegram's HMAC contract.

    The caller must use the raw ``initData`` query string, never
    ``initDataUnsafe``. Freshness is checked as well as integrity so a captured
    Mini App payload cannot be replayed indefinitely.
    """

    if not init_data:
        raise WebAppAuthError("missing initData")
    if len(init_data) > MAX_INIT_DATA_LENGTH:
        raise WebAppAuthError("initData is too large")
    if not bot_token:
        raise WebAppAuthError("bot token is not configured")

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise WebAppAuthError("malformed initData") from exc

    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise WebAppAuthError("duplicate initData fields")

    fields = dict(pairs)
    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise WebAppAuthError("missing initData hash")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items())
    )
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise WebAppAuthError("invalid initData hash")

    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WebAppAuthError("invalid auth_date") from exc

    current_time = time.time() if now is None else float(now)
    age = current_time - auth_date
    if age < -CLOCK_SKEW_SECONDS:
        raise WebAppAuthError("auth_date is in the future")
    if age > max_age_seconds:
        raise WebAppAuthError("initData is expired")

    try:
        user = json.loads(fields["user"])
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WebAppAuthError("invalid Telegram user") from exc

    start_param = fields.get("start_param") or None
    return TelegramWebAppIdentity(
        user_id=user_id,
        auth_date=auth_date,
        start_param=start_param,
    )


def normalize_crocodile_room(room: object) -> tuple[str, str]:
    """Return canonical Socket.IO room and Telegram chat id.

    Negative Telegram chat ids are encoded as ``m<digits>`` in ``startapp``
    because the Mini App start parameter must be URL-friendly.
    """

    value = str(room or "").strip()
    if not _ROOM_RE.fullmatch(value):
        raise WebAppAuthError("invalid crocodile room")

    if value.startswith("m"):
        numeric_chat_id = -int(value[1:])
        if numeric_chat_id == 0:
            raise WebAppAuthError("invalid crocodile chat id")
        chat_id = str(numeric_chat_id)
        canonical_room = f"m{abs(numeric_chat_id)}"
    else:
        numeric_chat_id = int(value)
        if numeric_chat_id == 0:
            raise WebAppAuthError("invalid crocodile chat id")
        chat_id = str(numeric_chat_id)
        canonical_room = (
            f"m{abs(numeric_chat_id)}" if numeric_chat_id < 0 else chat_id
        )

    return canonical_room, chat_id


def authorize_crocodile_drawer(
    room: object,
    user_id: int,
    game_sessions: Mapping[str, Mapping[str, object]],
) -> tuple[str, str]:
    """Authorize a verified Telegram user for the active drawing session."""

    canonical_room, chat_id = normalize_crocodile_room(room)
    session = game_sessions.get(chat_id)
    if not session:
        raise WebAppAuthError("crocodile session is not active")

    try:
        drawer_id = int(session.get("drawer_id"))
    except (TypeError, ValueError) as exc:
        raise WebAppAuthError("crocodile drawer is not configured") from exc

    if drawer_id != int(user_id):
        raise WebAppAuthError("Telegram user is not the active drawer")

    return canonical_room, chat_id
