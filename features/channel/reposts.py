"""Репосты постов Упупы в знакомых публичных каналах.

Bot API не отдаёт счётчик пересылок, поэтому видны только репосты в каналах из WATCHED_CHANNELS.
Лента t.me/s/<канал> показывает около 20 последних постов, и репосты опросов без текста в неё не попадают.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from datetime import datetime, timedelta, timezone

from core.paths import DATA_DIR
from features.channel import service as base
from features.channel.batya_source import BATYA_CHANNEL
from prompts.channel import REPOST_REACTION_PROMPT

REPOSTS_FILE = DATA_DIR / "channel_reposts.json"
MAX_STORED_REPOSTS = 500
FEED_LIMIT = 20
REACTION_PROBABILITY = 0.5
REACTION_COOLDOWN_POSTS = 12
REACTION_MAX_AGE_HOURS = 48
REACTION_MODE_NAME = "repost_reaction"

# Канал → как Упупа называет его автора.
WATCHED_CHANNELS = {
    BATYA_CHANNEL: "твой батя",
    "muhtarboodka": "Мухтар",
    "kapibara_fen": "автор канала @kapibara_fen",
}

_state_lock = asyncio.Lock()


def load_reposts() -> list[dict]:
    try:
        with REPOSTS_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _save_reposts(reposts: list[dict]) -> None:
    tmp_path = REPOSTS_FILE.with_name(REPOSTS_FILE.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(reposts[-MAX_STORED_REPOSTS:], fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, REPOSTS_FILE)


async def scan_for_reposts() -> int:
    """Запоминает новые репосты; недоступный канал просто пропускается до следующего раза."""
    found: list[dict] = []
    for channel in WATCHED_CHANNELS:
        for post in await base.fetch_public_posts(channel, limit=FEED_LIMIT):
            if base._is_own_repost(post):
                found.append(
                    {
                        "channel": channel,
                        "url": str(post.get("url") or ""),
                        "source_message_id": int(post["forwarded_from_message_id"]),
                        "posted_at": post.get("posted_at"),
                        "seen_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
    if not found:
        return 0

    async with _state_lock:
        reposts = await asyncio.to_thread(load_reposts)
        known_urls = {str(item.get("url")) for item in reposts}
        fresh = [item for item in found if item["url"] and item["url"] not in known_urls]
        if fresh:
            await asyncio.to_thread(_save_reposts, reposts + fresh)
    if fresh:
        logging.info("[channel] reposts detected urls=%s", [item["url"] for item in fresh])
    return len(fresh)


def _is_recent(record: dict, *, now: datetime) -> bool:
    try:
        posted_at = datetime.fromisoformat(str(record.get("posted_at") or record.get("seen_at")))
    except ValueError:
        return False
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    return now - posted_at <= timedelta(hours=REACTION_MAX_AGE_HOURS)


def pick_reaction_event(
    published_posts: list[dict],
    *,
    now: datetime | None = None,
    rng=random,
) -> dict | None:
    """Свежий репост, на который Упупа ещё не реагировал; реакции редкие, чтобы не стать рубрикой."""
    recent = published_posts[-REACTION_COOLDOWN_POSTS:]
    if any(post.get("content_mode") == REACTION_MODE_NAME for post in recent):
        return None
    if rng.random() >= REACTION_PROBABILITY:
        return None

    current = now or datetime.now(timezone.utc)
    used_urls = {post.get("repost_reaction_url") for post in published_posts if post.get("repost_reaction_url")}
    by_message_id = {post.get("message_id"): post for post in published_posts}
    for record in reversed(load_reposts()):
        if record.get("url") in used_urls or not _is_recent(record, now=current):
            continue
        own_post = by_message_id.get(record.get("source_message_id")) or {}
        post_text = re.sub(r"https?://\S+", "", str(own_post.get("text") or "")).strip()
        if post_text and record.get("channel") in WATCHED_CHANNELS:
            return {**record, "post_text": post_text}
    return None


def reaction_instruction(event: dict) -> str:
    return REPOST_REACTION_PROMPT.format(
        who=WATCHED_CHANNELS[event["channel"]],
        post_text=str(event["post_text"])[:300],
    )
