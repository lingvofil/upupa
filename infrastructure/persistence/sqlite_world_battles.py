"""Persistence for World of Upupa battles: 24-hour activity contests during a war."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class WorldBattle:
    battle_id: int
    state_a: int
    state_b: int
    started_by: int
    started_at: datetime
    ends_at: datetime
    finished_at: datetime | None = None
    score_a: float | None = None
    score_b: float | None = None
    winner: int | None = None
    details: dict | None = None

    @property
    def active(self) -> bool:
        return self.finished_at is None

    def opponent_of(self, world_id: int) -> int:
        return self.state_b if int(world_id) == self.state_a else self.state_a


def _dt(value: object) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class SQLiteWorldBattleRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            with conn:
                yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_battles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    state_a INTEGER NOT NULL,
                    state_b INTEGER NOT NULL,
                    started_by INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    finished_at TEXT,
                    score_a REAL,
                    score_b REAL,
                    winner INTEGER,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    CHECK(state_a < state_b)
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_world_battles_active
                ON world_battles(state_a, state_b) WHERE finished_at IS NULL
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_battles_due ON world_battles(finished_at, ends_at)"
            )

    @staticmethod
    def _battle(row: sqlite3.Row) -> WorldBattle:
        try:
            details = json.loads(str(row["details_json"] or "{}"))
        except (TypeError, ValueError):
            details = {}
        return WorldBattle(
            battle_id=int(row["id"]),
            state_a=int(row["state_a"]),
            state_b=int(row["state_b"]),
            started_by=int(row["started_by"]),
            started_at=_dt(row["started_at"]),  # type: ignore[arg-type]
            ends_at=_dt(row["ends_at"]),  # type: ignore[arg-type]
            finished_at=_dt(row["finished_at"]),
            score_a=(float(row["score_a"]) if row["score_a"] is not None else None),
            score_b=(float(row["score_b"]) if row["score_b"] is not None else None),
            winner=(int(row["winner"]) if row["winner"] is not None else None),
            details=details if isinstance(details, dict) else {},
        )

    def start_battle(
        self,
        attacker: int,
        defender: int,
        *,
        duration: timedelta,
        now: datetime | None = None,
    ) -> tuple[str, WorldBattle | None]:
        first, second = sorted((int(attacker), int(defender)))
        started = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT * FROM world_battles WHERE state_a = ? AND state_b = ? AND finished_at IS NULL",
                (first, second),
            ).fetchone()
            if active is not None:
                return "active_exists", self._battle(active)
            cursor = conn.execute(
                """
                INSERT INTO world_battles(state_a, state_b, started_by, started_at, ends_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (first, second, int(attacker), started.isoformat(), (started + duration).isoformat()),
            )
            row = conn.execute("SELECT * FROM world_battles WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return "started", self._battle(row)

    def get_battle(self, battle_id: int) -> WorldBattle | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM world_battles WHERE id = ?", (int(battle_id),)).fetchone()
        return self._battle(row) if row is not None else None

    def get_active_battle(self, state_a: int, state_b: int) -> WorldBattle | None:
        first, second = sorted((int(state_a), int(state_b)))
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_battles WHERE state_a = ? AND state_b = ? AND finished_at IS NULL",
                (first, second),
            ).fetchone()
        return self._battle(row) if row is not None else None

    def list_active_battles(self, world_id: int | None = None) -> list[WorldBattle]:
        sql = "SELECT * FROM world_battles WHERE finished_at IS NULL"
        params: tuple[object, ...] = ()
        if world_id is not None:
            sql += " AND (state_a = ? OR state_b = ?)"
            params = (int(world_id), int(world_id))
        with self._connect() as conn:
            rows = conn.execute(sql + " ORDER BY ends_at", params).fetchall()
        return [self._battle(row) for row in rows]

    def list_due_battles(self, now: datetime) -> list[WorldBattle]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_battles WHERE finished_at IS NULL AND ends_at <= ? ORDER BY ends_at",
                (now.isoformat(),),
            ).fetchall()
        return [self._battle(row) for row in rows]

    def finish_battle(
        self,
        battle_id: int,
        *,
        score_a: float | None,
        score_b: float | None,
        winner: int | None,
        details: dict | None = None,
        now: datetime | None = None,
    ) -> WorldBattle | None:
        """Record the result exactly once; a second caller gets ``None``."""
        finished = (now or datetime.now(timezone.utc)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE world_battles
                SET finished_at = ?, score_a = ?, score_b = ?, winner = ?, details_json = ?
                WHERE id = ? AND finished_at IS NULL
                """,
                (
                    finished,
                    score_a,
                    score_b,
                    winner,
                    json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                    int(battle_id),
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM world_battles WHERE id = ?", (int(battle_id),)).fetchone()
        return self._battle(row)

    def cancel_active_battles(self, state_a: int, state_b: int, *, reason: str) -> list[WorldBattle]:
        """Peace or capitulation stops the fight without a winner."""
        first, second = sorted((int(state_a), int(state_b)))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_battles WHERE state_a = ? AND state_b = ? AND finished_at IS NULL",
                (first, second),
            ).fetchall()
            conn.execute(
                """
                UPDATE world_battles SET finished_at = ?, details_json = ?
                WHERE state_a = ? AND state_b = ? AND finished_at IS NULL
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps({"cancelled": reason}, ensure_ascii=False),
                    first,
                    second,
                ),
            )
        return [self._battle(row) for row in rows]

    def list_recent_results(self, *, days: int = 30, world_id: int | None = None, limit: int = 20) -> list[WorldBattle]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        sql = "SELECT * FROM world_battles WHERE finished_at IS NOT NULL AND finished_at >= ?"
        params: list[object] = [since]
        if world_id is not None:
            sql += " AND (state_a = ? OR state_b = ?)"
            params.extend((int(world_id), int(world_id)))
        sql += " ORDER BY finished_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._battle(row) for row in rows]
