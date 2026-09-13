"""SQLite persistence adapter for deterministic chat social-graph events."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import time
from typing import Any, Sequence


RETENTION_DAYS = 90
CLEANUP_INTERVAL_SECONDS = 3600
_RELATIONSHIP_XP = {"reply": 1.0, "mention": 0.75, "reaction": 0.5}
_RELATIONSHIP_DAILY_XP_CAP = 12.0


class SQLiteSocialGraphRepository:
    """Store social interactions and durable pair state in statistics SQLite.

    Raw interactions remain a rolling window.  Relationship state is lifetime
    data and survives raw-event cleanup, allowing old connections to decay
    without being forgotten.
    """

    def __init__(self, path: str | Path, *, retention_days: int = RETENTION_DAYS) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        self._cleanup_lock = threading.Lock()
        self._last_cleanup = 0.0

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
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
                """
                CREATE TABLE IF NOT EXISTS social_relationships (
                    chat_id BIGINT NOT NULL,
                    user_a_id BIGINT NOT NULL,
                    user_b_id BIGINT NOT NULL,
                    xp REAL NOT NULL DEFAULT 0,
                    interaction_count INTEGER NOT NULL DEFAULT 0,
                    reply_count INTEGER NOT NULL DEFAULT 0,
                    mention_count INTEGER NOT NULL DEFAULT 0,
                    reaction_count INTEGER NOT NULL DEFAULT 0,
                    a_to_b_count INTEGER NOT NULL DEFAULT 0,
                    b_to_a_count INTEGER NOT NULL DEFAULT 0,
                    a_to_b_weight REAL NOT NULL DEFAULT 0,
                    b_to_a_weight REAL NOT NULL DEFAULT 0,
                    first_interaction_at TEXT,
                    last_interaction_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_a_id, user_b_id),
                    CHECK (user_a_id < user_b_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS social_relationship_daily (
                    chat_id BIGINT NOT NULL,
                    user_a_id BIGINT NOT NULL,
                    user_b_id BIGINT NOT NULL,
                    activity_date TEXT NOT NULL,
                    event_count INTEGER NOT NULL DEFAULT 0,
                    xp_earned REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (chat_id, user_a_id, user_b_id, activity_date)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS social_relationship_snapshots (
                    chat_id BIGINT NOT NULL,
                    user_a_id BIGINT NOT NULL,
                    user_b_id BIGINT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    affinity INTEGER NOT NULL,
                    tension INTEGER NOT NULL,
                    xp REAL NOT NULL,
                    level INTEGER NOT NULL,
                    reciprocity REAL NOT NULL,
                    archetype TEXT NOT NULL,
                    trend TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_a_id, user_b_id, snapshot_date)
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_relationships_user_a "
                "ON social_relationships(chat_id, user_a_id, xp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_relationships_user_b "
                "ON social_relationships(chat_id, user_b_id, xp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_social_relationship_snapshots_pair "
                "ON social_relationship_snapshots(chat_id, user_a_id, user_b_id, captured_at DESC)"
            )
            self._backfill_relationships(conn)

    def _backfill_relationships(self, conn: sqlite3.Connection) -> None:
        """Seed lifetime pair state once from retained raw interactions."""
        now = self._timestamp(datetime.now(timezone.utc))
        conn.execute(
            """
            INSERT OR IGNORE INTO social_relationships (
                chat_id,user_a_id,user_b_id,xp,interaction_count,reply_count,mention_count,
                reaction_count,a_to_b_count,b_to_a_count,a_to_b_weight,b_to_a_weight,
                first_interaction_at,last_interaction_at,updated_at
            )
            SELECT
                chat_id,
                CASE WHEN actor_user_id < target_user_id THEN actor_user_id ELSE target_user_id END AS user_a_id,
                CASE WHEN actor_user_id < target_user_id THEN target_user_id ELSE actor_user_id END AS user_b_id,
                SUM(CASE interaction_type WHEN 'reply' THEN 1.0 WHEN 'mention' THEN 0.75 WHEN 'reaction' THEN 0.5 ELSE 0.5 END),
                COUNT(*),
                SUM(CASE WHEN interaction_type='reply' THEN 1 ELSE 0 END),
                SUM(CASE WHEN interaction_type='mention' THEN 1 ELSE 0 END),
                SUM(CASE WHEN interaction_type='reaction' THEN 1 ELSE 0 END),
                SUM(CASE WHEN actor_user_id < target_user_id THEN 1 ELSE 0 END),
                SUM(CASE WHEN actor_user_id > target_user_id THEN 1 ELSE 0 END),
                SUM(CASE WHEN actor_user_id < target_user_id THEN weight ELSE 0 END),
                SUM(CASE WHEN actor_user_id > target_user_id THEN weight ELSE 0 END),
                MIN(interaction_timestamp),MAX(interaction_timestamp),?
            FROM social_interactions
            GROUP BY chat_id,user_a_id,user_b_id
            """,
            (now,),
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
            # Participants are retained: durable relationships still need stable names/usernames.
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

    def _record_relationship_event(
        self,
        conn: sqlite3.Connection,
        *,
        chat_id: int,
        actor_user_id: int,
        target_user_id: int,
        interaction_type: str,
        weight: float,
        timestamp: str,
    ) -> None:
        user_a_id, user_b_id = sorted((int(actor_user_id), int(target_user_id)))
        activity_date = timestamp[:10]
        row = conn.execute(
            """SELECT event_count,xp_earned FROM social_relationship_daily
               WHERE chat_id=? AND user_a_id=? AND user_b_id=? AND activity_date=?""",
            (chat_id, user_a_id, user_b_id, activity_date),
        ).fetchone()
        event_count = int(row["event_count"]) if row else 0
        xp_earned = float(row["xp_earned"]) if row else 0.0
        base_xp = _RELATIONSHIP_XP.get(interaction_type, 0.5)
        diminishing = base_xp / (1.0 + event_count * 0.15)
        xp_gain = min(diminishing, max(0.0, _RELATIONSHIP_DAILY_XP_CAP - xp_earned))
        conn.execute(
            """
            INSERT INTO social_relationship_daily
                (chat_id,user_a_id,user_b_id,activity_date,event_count,xp_earned)
            VALUES (?,?,?,?,1,?)
            ON CONFLICT(chat_id,user_a_id,user_b_id,activity_date) DO UPDATE SET
                event_count=event_count+1,
                xp_earned=xp_earned+excluded.xp_earned
            """,
            (chat_id, user_a_id, user_b_id, activity_date, xp_gain),
        )

        a_to_b = int(actor_user_id == user_a_id)
        b_to_a = 1 - a_to_b
        conn.execute(
            """
            INSERT INTO social_relationships (
                chat_id,user_a_id,user_b_id,xp,interaction_count,reply_count,mention_count,
                reaction_count,a_to_b_count,b_to_a_count,a_to_b_weight,b_to_a_weight,
                first_interaction_at,last_interaction_at,updated_at
            ) VALUES (?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(chat_id,user_a_id,user_b_id) DO UPDATE SET
                xp=xp+excluded.xp,
                interaction_count=interaction_count+1,
                reply_count=reply_count+excluded.reply_count,
                mention_count=mention_count+excluded.mention_count,
                reaction_count=reaction_count+excluded.reaction_count,
                a_to_b_count=a_to_b_count+excluded.a_to_b_count,
                b_to_a_count=b_to_a_count+excluded.b_to_a_count,
                a_to_b_weight=a_to_b_weight+excluded.a_to_b_weight,
                b_to_a_weight=b_to_a_weight+excluded.b_to_a_weight,
                last_interaction_at=CASE WHEN excluded.last_interaction_at > last_interaction_at
                                         THEN excluded.last_interaction_at ELSE last_interaction_at END,
                updated_at=excluded.updated_at
            """,
            (
                chat_id,
                user_a_id,
                user_b_id,
                xp_gain,
                int(interaction_type == "reply"),
                int(interaction_type == "mention"),
                int(interaction_type == "reaction"),
                a_to_b,
                b_to_a,
                float(weight) if a_to_b else 0.0,
                float(weight) if b_to_a else 0.0,
                timestamp,
                timestamp,
                timestamp,
            ),
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
                if cursor.rowcount > 0:
                    inserted += 1
                    self._record_relationship_event(
                        conn,
                        chat_id=chat_id,
                        actor_user_id=actor[0],
                        target_user_id=target_user_id,
                        interaction_type=interaction_type,
                        weight=float(weight),
                        timestamp=timestamp_str,
                    )
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
        return int(row[0]) if row else None

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
            if cursor.rowcount > 0:
                self._record_relationship_event(
                    conn,
                    chat_id=chat_id,
                    actor_user_id=actor[0],
                    target_user_id=target_user_id,
                    interaction_type=interaction_type,
                    weight=float(weight),
                    timestamp=timestamp_str,
                )
                return True
            return False

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
                names.update({int(row["user_id"]): row["display_name"] for row in participant_rows})

        for user_id in user_ids:
            names.setdefault(int(user_id), "Участник")
        return [tuple(row) for row in rows], names

    def load_relationship_states(self, chat_id: int, user_id: int | None = None) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        recent_cutoff = self._timestamp(now - timedelta(days=7))
        previous_cutoff = self._timestamp(now - timedelta(days=28))
        clauses = ["r.chat_id=?"]
        args: list[Any] = [recent_cutoff, previous_cutoff, recent_cutoff, int(chat_id)]
        if user_id is not None:
            clauses.append("(r.user_a_id=? OR r.user_b_id=?)")
            args.extend((int(user_id), int(user_id)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*,
                       COALESCE(pa.display_name,'Участник') AS user_a_name,
                       COALESCE(pb.display_name,'Участник') AS user_b_name,
                       (SELECT COUNT(*) FROM social_interactions i
                        WHERE i.chat_id=r.chat_id
                          AND ((i.actor_user_id=r.user_a_id AND i.target_user_id=r.user_b_id)
                            OR (i.actor_user_id=r.user_b_id AND i.target_user_id=r.user_a_id))
                          AND i.interaction_timestamp>=?) AS recent_7d,
                       (SELECT COUNT(*) FROM social_interactions i
                        WHERE i.chat_id=r.chat_id
                          AND ((i.actor_user_id=r.user_a_id AND i.target_user_id=r.user_b_id)
                            OR (i.actor_user_id=r.user_b_id AND i.target_user_id=r.user_a_id))
                          AND i.interaction_timestamp>=? AND i.interaction_timestamp<?) AS previous_21d
                FROM social_relationships r
                LEFT JOIN social_participants pa ON pa.chat_id=r.chat_id AND pa.user_id=r.user_a_id
                LEFT JOIN social_participants pb ON pb.chat_id=r.chat_id AND pb.user_id=r.user_b_id
                WHERE """ + " AND ".join(clauses) + " ORDER BY r.xp DESC,r.last_interaction_at DESC",
                args,
            ).fetchall()
        return [dict(row) for row in rows]

    def save_relationship_snapshot(
        self,
        *,
        chat_id: int,
        user_a_id: int,
        user_b_id: int,
        affinity: int,
        tension: int,
        xp: float,
        level: int,
        reciprocity: float,
        archetype: str,
        trend: str,
        captured_at: datetime,
    ) -> None:
        user_a_id, user_b_id = sorted((int(user_a_id), int(user_b_id)))
        captured = self._timestamp(captured_at)
        snapshot_date = captured[:10]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO social_relationship_snapshots (
                    chat_id,user_a_id,user_b_id,snapshot_date,captured_at,affinity,tension,xp,
                    level,reciprocity,archetype,trend
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(chat_id,user_a_id,user_b_id,snapshot_date) DO UPDATE SET
                    captured_at=excluded.captured_at,
                    affinity=excluded.affinity,
                    tension=excluded.tension,
                    xp=excluded.xp,
                    level=excluded.level,
                    reciprocity=excluded.reciprocity,
                    archetype=excluded.archetype,
                    trend=excluded.trend
                """,
                (
                    int(chat_id), user_a_id, user_b_id, snapshot_date, captured,
                    int(affinity), int(tension), float(xp), int(level), float(reciprocity),
                    archetype, trend,
                ),
            )

    def load_relationship_snapshots(
        self,
        chat_id: int,
        user_a_id: int,
        user_b_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        user_a_id, user_b_id = sorted((int(user_a_id), int(user_b_id)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT captured_at,affinity,tension,xp,level,reciprocity,archetype,trend
                FROM social_relationship_snapshots
                WHERE chat_id=? AND user_a_id=? AND user_b_id=?
                ORDER BY captured_at DESC LIMIT ?
                """,
                (int(chat_id), user_a_id, user_b_id, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]
