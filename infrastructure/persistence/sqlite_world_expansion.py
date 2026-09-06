"""Persistence for sanctions and international court extensions of World Upupa."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class WorldSanction:
    sanction_id: int
    source_state: int
    target_state: int
    reason: str
    created_at: datetime


@dataclass(frozen=True)
class WorldCourtCase:
    case_id: int
    plaintiff_state: int
    defendant_state: int
    claim: str
    verdict: str
    created_at: datetime


class SQLiteWorldExpansionRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_sanctions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_state INTEGER NOT NULL,
                    target_state INTEGER NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    lifted_at TEXT,
                    CHECK(source_state <> target_state),
                    FOREIGN KEY(source_state) REFERENCES world_states(world_id),
                    FOREIGN KEY(target_state) REFERENCES world_states(world_id)
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_world_sanctions_active
                ON world_sanctions(source_state, target_state)
                WHERE lifted_at IS NULL
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_sanctions_target ON world_sanctions(target_state, lifted_at)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_court_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plaintiff_state INTEGER NOT NULL,
                    defendant_state INTEGER NOT NULL,
                    claim TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(plaintiff_state) REFERENCES world_states(world_id),
                    FOREIGN KEY(defendant_state) REFERENCES world_states(world_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_court_cases_created ON world_court_cases(created_at DESC, id DESC)"
            )

    @staticmethod
    def _sanction(row: sqlite3.Row) -> WorldSanction:
        return WorldSanction(
            sanction_id=int(row["id"]),
            source_state=int(row["source_state"]),
            target_state=int(row["target_state"]),
            reason=str(row["reason"] or ""),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    @staticmethod
    def _case(row: sqlite3.Row) -> WorldCourtCase:
        return WorldCourtCase(
            case_id=int(row["id"]),
            plaintiff_state=int(row["plaintiff_state"]),
            defendant_state=int(row["defendant_state"]),
            claim=str(row["claim"]),
            verdict=str(row["verdict"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def impose_sanction(self, source_state: int, target_state: int, reason: str) -> tuple[bool, WorldSanction | None]:
        clean_reason = " ".join((reason or "").split())[:300]
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO world_sanctions(source_state, target_state, reason, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (source_state, target_state, clean_reason, self._now()),
                )
                row = conn.execute("SELECT * FROM world_sanctions WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return True, self._sanction(row) if row is not None else None
        except sqlite3.IntegrityError:
            return False, None

    def lift_sanction(self, source_state: int, target_state: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE world_sanctions
                SET lifted_at = ?
                WHERE source_state = ? AND target_state = ? AND lifted_at IS NULL
                """,
                (self._now(), source_state, target_state),
            )
        return cursor.rowcount > 0

    def list_active_sanctions(self, world_id: int | None = None) -> list[WorldSanction]:
        params: tuple[object, ...] = ()
        where = "WHERE lifted_at IS NULL"
        if world_id is not None:
            where += " AND (source_state = ? OR target_state = ?)"
            params = (world_id, world_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM world_sanctions {where} ORDER BY created_at DESC, id DESC",
                params,
            ).fetchall()
        return [self._sanction(row) for row in rows]

    def record_court_case(self, plaintiff_state: int, defendant_state: int, claim: str, verdict: str) -> WorldCourtCase:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO world_court_cases(plaintiff_state, defendant_state, claim, verdict, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (plaintiff_state, defendant_state, claim[:1000], verdict[:6000], self._now()),
            )
            row = conn.execute("SELECT * FROM world_court_cases WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if row is None:
            raise RuntimeError("International court case was not stored")
        return self._case(row)

    def list_court_cases(self, *, limit: int = 10) -> list[WorldCourtCase]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_court_cases ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, min(int(limit), 50)),),
            ).fetchall()
        return [self._case(row) for row in rows]
