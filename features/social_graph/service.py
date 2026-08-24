"""Application service and Telegram capture middleware for the chat social graph."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Protocol, Sequence

from aiogram import BaseMiddleware
from aiogram.types import Message, MessageReactionUpdated

from core.state import chat_settings


REPLY_WEIGHT = 3.0
MENTION_WEIGHT = 2.0
REACTION_WEIGHT = 1.0
DEFAULT_PERIOD_DAYS = 30
RETENTION_DAYS = 90


class SocialGraphRepository(Protocol):
    def init_schema(self) -> None: ...

    def record_message_bundle(
        self,
        chat_id: int,
        message_id: int,
        timestamp: datetime,
        actor: tuple[int, str, str | None],
        participants: Sequence[tuple[int, str, str | None]],
        interactions: Sequence[tuple[int, str, float]],
    ) -> int: ...

    def resolve_usernames(
        self,
        chat_id: int,
        usernames: Sequence[str],
    ) -> dict[str, tuple[int, str, str | None]]: ...

    def resolve_message_author(self, chat_id: int, message_id: int) -> int | None: ...

    def record_interaction(
        self,
        chat_id: int,
        actor: tuple[int, str, str | None],
        target_user_id: int,
        interaction_type: str,
        message_id: int,
        timestamp: datetime,
        weight: float,
    ) -> bool: ...

    def load_graph(
        self,
        chat_id: int,
        since: datetime,
    ) -> tuple[list[tuple[int, int, str, float]], dict[int, str]]: ...


_repository_instance: SocialGraphRepository | None = None


@dataclass(frozen=True)
class SocialGraphData:
    interactions: tuple[tuple[int, int, str, float], ...]
    names: dict[int, str]
    period_days: int


def configure_social_graph_repository(repository: SocialGraphRepository) -> None:
    global _repository_instance
    _repository_instance = repository


def _repository() -> SocialGraphRepository:
    if _repository_instance is None:
        raise RuntimeError("Social graph repository is not configured")
    return _repository_instance


def init_db() -> None:
    _repository().init_schema()


def is_social_graph_enabled(chat_id: int) -> bool:
    return chat_settings.get(str(chat_id), {}).get("social_graph_enabled", True)


def _is_group(chat_type) -> bool:
    value = getattr(chat_type, "value", chat_type)
    return value in {"group", "supergroup"}


def _display_name(user) -> str:
    full_name = getattr(user, "full_name", None) or getattr(user, "first_name", None) or "Участник"
    username = getattr(user, "username", None)
    if username:
        return f"{full_name} (@{username})"
    return full_name


def _participant(user) -> tuple[int, str, str | None]:
    return user.id, _display_name(user), getattr(user, "username", None)


def _extract_entity_text(text: str, offset: int, length: int) -> str:
    encoded = text.encode("utf-16-le")
    return encoded[offset * 2 : (offset + length) * 2].decode("utf-16-le")


async def capture_message(message: Message) -> int:
    if not _is_group(message.chat.type) or not message.from_user or message.from_user.is_bot:
        return 0
    if not is_social_graph_enabled(message.chat.id):
        return 0

    actor = message.from_user
    participants: dict[int, tuple[int, str, str | None]] = {actor.id: _participant(actor)}
    interactions: set[tuple[int, str, float]] = set()

    replied = getattr(message, "reply_to_message", None)
    target_user = getattr(replied, "from_user", None) if replied else None
    if target_user and not target_user.is_bot and target_user.id != actor.id:
        participants[target_user.id] = _participant(target_user)
        interactions.add((target_user.id, "reply", REPLY_WEIGHT))

    text = message.text or message.caption or ""
    entities = message.entities if message.text else message.caption_entities
    unresolved_usernames: set[str] = set()
    if text and entities:
        for entity in entities:
            entity_type = str(entity.type).lower()
            if entity_type.endswith("text_mention") and entity.user and not entity.user.is_bot:
                target = entity.user
                if target.id != actor.id:
                    participants[target.id] = _participant(target)
                    interactions.add((target.id, "mention", MENTION_WEIGHT))
            elif entity_type.endswith("mention"):
                raw = _extract_entity_text(text, entity.offset, entity.length).strip()
                if raw.startswith("@") and len(raw) > 1:
                    unresolved_usernames.add(raw[1:].lower())

    if unresolved_usernames:
        resolved = await asyncio.to_thread(
            _repository().resolve_usernames,
            message.chat.id,
            sorted(unresolved_usernames),
        )
        for _username, target in resolved.items():
            target_id, _display, _known_username = target
            if target_id == actor.id:
                continue
            participants[target_id] = target
            interactions.add((target_id, "mention", MENTION_WEIGHT))

    timestamp = message.date or datetime.now(timezone.utc)
    return await asyncio.to_thread(
        _repository().record_message_bundle,
        message.chat.id,
        message.message_id,
        timestamp,
        _participant(actor),
        tuple(participants.values()),
        tuple(sorted(interactions)),
    )


async def capture_reaction(update: MessageReactionUpdated) -> bool:
    if not _is_group(update.chat.type) or not is_social_graph_enabled(update.chat.id):
        return False
    actor = update.user
    if actor is None or actor.is_bot:
        return False
    if update.old_reaction or not update.new_reaction:
        return False

    target_user_id = await asyncio.to_thread(
        _repository().resolve_message_author,
        update.chat.id,
        update.message_id,
    )
    if target_user_id is None or target_user_id == actor.id:
        return False

    return await asyncio.to_thread(
        _repository().record_interaction,
        update.chat.id,
        _participant(actor),
        target_user_id,
        "reaction",
        update.message_id,
        update.date or datetime.now(timezone.utc),
        REACTION_WEIGHT,
    )


async def get_graph_data(chat_id: int, *, period_days: int = DEFAULT_PERIOD_DAYS) -> SocialGraphData:
    since = datetime.now(timezone.utc) - timedelta(days=period_days)
    interactions, names = await asyncio.to_thread(_repository().load_graph, chat_id, since)
    return SocialGraphData(tuple(interactions), names, period_days)


class SocialInteractionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data):
        try:
            await capture_message(event)
        except Exception as exc:
            logging.error("Failed to capture social interaction: %s", exc)
        return await handler(event, data)
