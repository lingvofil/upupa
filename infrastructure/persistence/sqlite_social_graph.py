"""SQLite persistence adapter for deterministic chat social-graph events."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import time
from typing import Sequence


RETENTION_DAYS = 90
CLEANUP_INTERVAL_SECONDS = 3600


class SQLiteSocialGraphRepository:
    """Store social interactions in the shared statistics SQLite database.

    The adapter is synchronous by design; async callers must offload it with
    ``asyncio.to_thread``. Every event stays deduplicated by message and signal.
    """

    def __init__(self, path: str | Path, *, retention_days: int = RETENTION_DAYS) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        self._cleanup_lock = threading.Lock()
        self._last_cleanup = 0.0

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _timestamp(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _participant_display(display_name: str, username: str | None) -> str:
        display_name = (display_name or "Участник").strip() or "Участник"
        if username and f"@{username.lower()}" not in display_name.lower():
            return f"{display_name} (@{username})"
        return display_name

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS social_participants (
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    display_name TEXT NOT NULL,
                    username TEXT,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS social_message_authors (
                    chat_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    message_timestamp TEXT NOT NULL,
                    PRIMARY KEY (chat_id, message_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS social_interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id BIGINT NOT NULL,
                    actor_user_id BIGINT NOT NULL,
                    target_user_id BIGINT NOT NULL,
                    interaction_type TEXT NOT NULL,
                    message_id BIGINT NOT NULL,
                    interaction_timestamp TEXT NOT NULL,
                    weight REAL NOT NULL,
                    CHECK (actor_user_id <> target_user_id),
                    CHECK (weight > 0),
                    UNIQUE (chat_id, actor_user_id, target_user_id, interaction_type, message_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_interactions_chat_time "
                "ON social_interactions(chat_id, interaction_timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_interactions_actor "
                "ON social_interactions(chat_id, actor_user_id, interaction_timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_interactions_target "
                "ON social_interactions(chat_id, target_user_id, interaction_timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_participants_username "
                "ON social_participants(chat_id, username COLLATE NOCASE)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_messages_time "
                "ON social_message_authors(chat_id, message_timestamp)"
            )

    def _cleanup_if_due(self, conn: sqlite3.Connection) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < CLEANUP_INTERVAL_SECONDS:
            return
        with self._cleanup_lock:
            now = time.monotonic()
            if now - self._last_cleanup < CLEANUP_INTERVAL_SECONDS:
                return
            cutoff = self._timestamp(datetime.now(timezone.utc) - timedelta(days=self.retention_days))
            conn.execute("DELETE FROM social_interactions WHERE interaction_timestamp < ?", (cutoff,))
            conn.execute("DELETE FROM social_message_authors WHERE message_timestamp < ?", (cutoff,))
            conn.execute("DELETE FROM social_participants WHERE last_seen < ?", (cutoff,))
            self._last_cleanup = now

    @staticmethod
    def _upsert_participant(
        conn: sqlite3.Connection,
        chat_id: int,
        participant: tuple[int, str, str | None],
        timestamp: str,
    ) -> None:
        user_id, display_name, username = participant
        normalized_username = username.lower() if username else None
        conn.execute(
            """
            INSERT INTO social_participants (chat_id, user_id, display_name, username, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                display_name = excluded.display_name,
                username = excluded.username,
                last_seen = excluded.last_seen
            """,
            (chat_id, user_id, display_name, normalized_username, timestamp),
        )

    def record_message_bundle(
        self,
        chat_id: int,
        message_id: int,
        timestamp: datetime,
        actor: tuple[int, str, str | None],
        participants: Sequence[tuple[int, str, str | None]],
        interactions: Sequence[tuple[int, str, float]],
    ) -> int:
        timestamp_str = self._timestamp(timestamp)
        inserted = 0
        with self._connect() as conn:
            self._cleanup_if_due(conn)
            for participant in {item[0]: item for item in participants}.values():
                self._upsert_participant(conn, chat_id, participant, timestamp_str)
            self._upsert_participant(conn, chat_id, actor, timestamp_str)
            conn.execute(
                """
                INSERT INTO social_message_authors (chat_id, message_id, user_id, message_timestamp)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    message_timestamp = excluded.message_timestamp
                """,
                (chat_id, message_id, actor[0], timestamp_str),
            )
            for target_user_id, interaction_type, weight in interactions:
                if target_user_id == actor[0]:
                    continue
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO social_interactions
                        (chat_id, actor_user_id, target_user_id, interaction_type,
                         message_id, interaction_timestamp, weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        actor[0],
                        target_user_id,
                        interaction_type,
                        message_id,
                        timestamp_str,
                        float(weight),
                    ),
                )
                inserted += max(cursor.rowcount, 0)
        return inserted

    def resolve_usernames(
        self,
        chat_id: int,
        usernames: Sequence[str],
    ) -> dict[str, tuple[int, str, str | None]]:
        normalized = sorted({username.lower().lstrip("@") for username in usernames if username})
        if not normalized:
            return {}

        result: dict[str, tuple[int, str, str | None]] = {}
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in normalized)
            rows = conn.execute(
                f"""
                SELECT user_id, display_name, username
                FROM social_participants
                WHERE chat_id = ? AND username COLLATE NOCASE IN ({placeholders})
                """,
                [chat_id, *normalized],
            ).fetchall()
            for user_id, display_name, username in rows:
                if username:
                    result[username.lower()] = (user_id, display_name, username)

            # message_stats predates the social graph and is a safe same-chat fallback
            # for a mentioned user who has not spoken since this feature was enabled.
            for username in normalized:
                if username in result:
                    continue
                try:
                    row = conn.execute(
                        """
                        SELECT user_id, user_name, user_username
                        FROM message_stats
                        WHERE chat_id = ? AND lower(user_username) = ?
                        ORDER BY message_timestamp DESC
                        LIMIT 1
                        """,
                        (chat_id, username),
                    ).fetchone()
                except sqlite3.OperationalError:
                    row = None
                if not row:
                    continue
                user_id, user_name, known_username = row
                display = self._participant_display(user_name or "Участник", known_username)
                result[username] = (user_id, display, known_username)
        return result

    def resolve_message_author(self, chat_id: int, message_id: int) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id FROM social_message_authors WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()
        return row[0] if row else None

    def record_interaction(
        self,
        chat_id: int,
        actor: tuple[int, str, str | None],
        target_user_id: int,
        interaction_type: str,
        message_id: int,
        timestamp: datetime,
        weight: float,
    ) -> bool:
        if actor[0] == target_user_id or weight <= 0:
            return False
        timestamp_str = self._timestamp(timestamp)
        with self._connect() as conn:
            self._cleanup_if_due(conn)
            self._upsert_participant(conn, chat_id, actor, timestamp_str)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO social_interactions
                    (chat_id, actor_user_id, target_user_id, interaction_type,
                     message_id, interaction_timestamp, weight)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    actor[0],
                    target_user_id,
                    interaction_type,
                    message_id,
                    timestamp_str,
                    float(weight),
                ),
            )
            return cursor.rowcount > 0

    def load_graph(
        self,
        chat_id: int,
        since: datetime,
    ) -> tuple[list[tuple[int, int, str, float]], dict[int, str]]:
        since_str = self._timestamp(since)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT actor_user_id, target_user_id, interaction_type, weight
                FROM social_interactions
                WHERE chat_id = ? AND interaction_timestamp >= ?
                ORDER BY interaction_timestamp ASC, id ASC
                """,
                (chat_id, since_str),
            ).fetchall()
            user_ids = sorted({user_id for row in rows for user_id in row[:2]})
            names: dict[int, str] = {}
            if user_ids:
                placeholders = ",".join("?" for _ in user_ids)
                participant_rows = conn.execute(
                    f"""
                    SELECT user_id, display_name
                    FROM social_participants
                    WHERE chat_id = ? AND user_id IN ({placeholders})
                    """,
                    [chat_id, *user_ids],
                ).fetchall()
                names.update({user_id: display_name for user_id, display_name in participant_rows})

        for user_id in user_ids:
            names.setdefault(user_id, "Участник")
        return [tuple(row) for row in rows], names
