"""SQLite adapter for World of Upupa states and diplomacy."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from features.world.models import DiplomaticRequest, WorldState


class SQLiteWorldRepository:
    """Own all SQL and transactions for the inter-chat world database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _state(row: sqlite3.Row | None) -> WorldState | None:
        if row is None:
            return None
        return WorldState(
            world_id=int(row["world_id"]),
            chat_id=int(row["chat_id"]),
            title=str(row["chat_title"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            enabled=bool(row["enabled"]),
        )

    @staticmethod
    def _request(row: sqlite3.Row | None) -> DiplomaticRequest | None:
        if row is None:
            return None
        return DiplomaticRequest(
            request_id=int(row["id"]),
            source_state=int(row["source_state"]),
            target_state=int(row["target_state"]),
            status=str(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=(
                datetime.fromisoformat(row["resolved_at"])
                if row["resolved_at"]
                else None
            ),
        )

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_states (
                    world_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id BIGINT NOT NULL UNIQUE,
                    chat_title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS diplomatic_relations (
                    state_a INTEGER NOT NULL,
                    state_b INTEGER NOT NULL,
                    relation TEXT NOT NULL CHECK(relation = 'allied'),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (state_a, state_b),
                    CHECK(state_a < state_b),
                    FOREIGN KEY(state_a) REFERENCES world_states(world_id),
                    FOREIGN KEY(state_b) REFERENCES world_states(world_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS diplomatic_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_state INTEGER NOT NULL,
                    target_state INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'rejected', 'cancelled')),
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    CHECK(source_state <> target_state),
                    FOREIGN KEY(source_state) REFERENCES world_states(world_id),
                    FOREIGN KEY(target_state) REFERENCES world_states(world_id)
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_diplomatic_pending_pair
                ON diplomatic_requests(
                    MIN(source_state, target_state),
                    MAX(source_state, target_state)
                )
                WHERE status = 'pending'
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_states_enabled ON world_states(enabled, world_id)"
            )

    def enable_state(self, chat_id: int, title: str) -> WorldState:
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM world_states WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO world_states(chat_id, chat_title, created_at, updated_at, enabled)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (chat_id, title, now, now),
                )
                world_id = int(cursor.lastrowid)
            else:
                world_id = int(row["world_id"])
                conn.execute(
                    """
                    UPDATE world_states
                    SET chat_title = ?, updated_at = ?, enabled = 1
                    WHERE world_id = ?
                    """,
                    (title, now, world_id),
                )
            state = conn.execute(
                "SELECT * FROM world_states WHERE world_id = ?",
                (world_id,),
            ).fetchone()
        return self._state(state)  # type: ignore[return-value]

    def disable_state(self, chat_id: int) -> WorldState | None:
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM world_states WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            if row is None:
                return None
            world_id = int(row["world_id"])
            conn.execute(
                "UPDATE world_states SET enabled = 0, updated_at = ? WHERE world_id = ?",
                (now, world_id),
            )
            conn.execute(
                """
                UPDATE diplomatic_requests
                SET status = 'cancelled', resolved_at = ?
                WHERE status = 'pending' AND (source_state = ? OR target_state = ?)
                """,
                (now, world_id, world_id),
            )
            updated = conn.execute(
                "SELECT * FROM world_states WHERE world_id = ?",
                (world_id,),
            ).fetchone()
        return self._state(updated)

    def update_title(self, chat_id: int, title: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE world_states
                SET chat_title = ?, updated_at = ?
                WHERE chat_id = ? AND chat_title <> ?
                """,
                (title, self._now(), chat_id, title),
            )

    def get_state_by_chat_id(self, chat_id: int) -> WorldState | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_states WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return self._state(row)

    def get_state_by_world_id(self, world_id: int) -> WorldState | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_states WHERE world_id = ?",
                (world_id,),
            ).fetchone()
        return self._state(row)

    def list_enabled_states(self, exclude_world_id: int | None = None) -> list[WorldState]:
        sql = "SELECT * FROM world_states WHERE enabled = 1"
        params: tuple[int, ...] = ()
        if exclude_world_id is not None:
            sql += " AND world_id <> ?"
            params = (exclude_world_id,)
        sql += " ORDER BY world_id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._state(row) for row in rows if row is not None]  # type: ignore[misc]

    def list_allied_states(self, world_id: int, *, active_only: bool) -> list[WorldState]:
        enabled_filter = "AND ws.enabled = 1" if active_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT ws.*
                FROM diplomatic_relations dr
                JOIN world_states ws
                  ON ws.world_id = CASE
                      WHEN dr.state_a = ? THEN dr.state_b
                      ELSE dr.state_a
                  END
                WHERE (dr.state_a = ? OR dr.state_b = ?)
                  AND dr.relation = 'allied'
                  {enabled_filter}
                ORDER BY ws.world_id
                """,
                (world_id, world_id, world_id),
            ).fetchall()
        return [self._state(row) for row in rows if row is not None]  # type: ignore[misc]

    def has_alliance(self, state_a: int, state_b: int) -> bool:
        first, second = sorted((state_a, state_b))
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM diplomatic_relations
                WHERE state_a = ? AND state_b = ? AND relation = 'allied'
                """,
                (first, second),
            ).fetchone()
        return row is not None

    def get_pending_request_between(self, state_a: int, state_b: int) -> DiplomaticRequest | None:
        first, second = sorted((state_a, state_b))
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM diplomatic_requests
                WHERE status = 'pending'
                  AND MIN(source_state, target_state) = ?
                  AND MAX(source_state, target_state) = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (first, second),
            ).fetchone()
        return self._request(row)

    def create_request(
        self,
        source_state: int,
        target_state: int,
    ) -> tuple[str, DiplomaticRequest | None]:
        """Atomically validate both states/relation and create at most one pending request."""
        now = self._now()
        first, second = sorted((source_state, target_state))

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")

            source_row = conn.execute(
                "SELECT enabled FROM world_states WHERE world_id = ?",
                (source_state,),
            ).fetchone()
            if source_row is None or not bool(source_row["enabled"]):
                return "source_disabled", None

            target_row = conn.execute(
                "SELECT enabled FROM world_states WHERE world_id = ?",
                (target_state,),
            ).fetchone()
            if target_row is None or not bool(target_row["enabled"]):
                return "target_disabled", None

            alliance = conn.execute(
                """
                SELECT 1 FROM diplomatic_relations
                WHERE state_a = ? AND state_b = ? AND relation = 'allied'
                """,
                (first, second),
            ).fetchone()
            if alliance is not None:
                return "already_allied", None

            existing_row = conn.execute(
                """
                SELECT * FROM diplomatic_requests
                WHERE status = 'pending'
                  AND MIN(source_state, target_state) = ?
                  AND MAX(source_state, target_state) = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (first, second),
            ).fetchone()
            if existing_row is not None:
                return "duplicate", self._request(existing_row)

            cursor = conn.execute(
                """
                INSERT INTO diplomatic_requests(source_state, target_state, status, created_at)
                VALUES (?, ?, 'pending', ?)
                """,
                (source_state, target_state, now),
            )
            request_id = int(cursor.lastrowid)
            row = conn.execute(
                "SELECT * FROM diplomatic_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            return "created", self._request(row)

    def cancel_request(self, request_id: int) -> bool:
        now = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE diplomatic_requests
                SET status = 'cancelled', resolved_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (now, request_id),
            )
        return cursor.rowcount > 0

    def resolve_request(
        self,
        request_id: int,
        target_chat_id: int,
        decision: str,
    ) -> tuple[str, DiplomaticRequest | None, WorldState | None, WorldState | None]:
        if decision not in {"accepted", "rejected"}:
            raise ValueError(f"Unsupported diplomatic decision: {decision}")

        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM diplomatic_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            request = self._request(row)
            if request is None:
                return "not_found", None, None, None

            source_row = conn.execute(
                "SELECT * FROM world_states WHERE world_id = ?",
                (request.source_state,),
            ).fetchone()
            target_row = conn.execute(
                "SELECT * FROM world_states WHERE world_id = ?",
                (request.target_state,),
            ).fetchone()
            source = self._state(source_row)
            target = self._state(target_row)

            if target is None or target.chat_id != target_chat_id:
                return "wrong_target", request, source, target
            if request.status != "pending":
                return "already_resolved", request, source, target
            if not target.enabled:
                conn.execute(
                    """
                    UPDATE diplomatic_requests
                    SET status = 'cancelled', resolved_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, request_id),
                )
                cancelled = conn.execute(
                    "SELECT * FROM diplomatic_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                return "disabled", self._request(cancelled), source, target
            if decision == "accepted" and (source is None or not source.enabled):
                conn.execute(
                    """
                    UPDATE diplomatic_requests
                    SET status = 'cancelled', resolved_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (now, request_id),
                )
                cancelled = conn.execute(
                    "SELECT * FROM diplomatic_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                return "disabled", self._request(cancelled), source, target

            cursor = conn.execute(
                """
                UPDATE diplomatic_requests
                SET status = ?, resolved_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (decision, now, request_id),
            )
            if cursor.rowcount != 1:
                latest = conn.execute(
                    "SELECT * FROM diplomatic_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                return "already_resolved", self._request(latest), source, target

            if decision == "accepted":
                first, second = sorted((request.source_state, request.target_state))
                conn.execute(
                    """
                    INSERT INTO diplomatic_relations(state_a, state_b, relation, updated_at)
                    VALUES (?, ?, 'allied', ?)
                    ON CONFLICT(state_a, state_b)
                    DO UPDATE SET relation = 'allied', updated_at = excluded.updated_at
                    """,
                    (first, second, now),
                )

            resolved = conn.execute(
                "SELECT * FROM diplomatic_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
        return decision, self._request(resolved), source, target

    def break_alliance(self, state_a: int, state_b: int) -> bool:
        first, second = sorted((state_a, state_b))
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM diplomatic_relations WHERE state_a = ? AND state_b = ?",
                (first, second),
            )
        return cursor.rowcount > 0
