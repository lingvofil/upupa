"""Persistence for sanctions and international court extensions of World Upupa."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3


SANCTION_DURATION = timedelta(days=7)


@dataclass(frozen=True)
class WorldSanction:
    sanction_id: int
    source_state: int
    target_state: int
    reason: str
    created_at: datetime
    expires_at: datetime | None = None


@dataclass(frozen=True)
class WorldCourtCase:
    case_id: int
    plaintiff_state: int
    defendant_state: int
    claim: str
    verdict: str
    created_at: datetime
    status: str = "decided"
    defense: str = ""
    defense_deadline: datetime | None = None
    outcome: str | None = None
    summons_message_id: int | None = None

    @property
    def pending(self) -> bool:
        return self.status == "awaiting_defense"


class SQLiteWorldExpansionRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

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
            sanction_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(world_sanctions)")}
            if "expires_at" not in sanction_columns:
                conn.execute("ALTER TABLE world_sanctions ADD COLUMN expires_at TEXT")
                # Sanctions used to be eternal; give the ones in force a fresh week.
                conn.execute(
                    "UPDATE world_sanctions SET expires_at = ? WHERE lifted_at IS NULL",
                    ((datetime.now(timezone.utc) + SANCTION_DURATION).isoformat(),),
                )
            case_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(world_court_cases)")}
            for name, ddl in (
                ("status", "TEXT NOT NULL DEFAULT 'decided'"),
                ("defense", "TEXT NOT NULL DEFAULT ''"),
                ("defense_deadline", "TEXT"),
                ("outcome", "TEXT"),
                ("summons_message_id", "INTEGER"),
            ):
                if name not in case_columns:
                    conn.execute(f"ALTER TABLE world_court_cases ADD COLUMN {name} {ddl}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_court_cases_status ON world_court_cases(status, defense_deadline)"
            )

    @staticmethod
    def _sanction(row: sqlite3.Row) -> WorldSanction:
        return WorldSanction(
            sanction_id=int(row["id"]),
            source_state=int(row["source_state"]),
            target_state=int(row["target_state"]),
            reason=str(row["reason"] or ""),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            expires_at=(
                datetime.fromisoformat(str(row["expires_at"]))
                if "expires_at" in row.keys() and row["expires_at"]
                else None
            ),
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
            status=str(row["status"]) if "status" in row.keys() else "decided",
            defense=str(row["defense"] or "") if "defense" in row.keys() else "",
            defense_deadline=(
                datetime.fromisoformat(str(row["defense_deadline"]))
                if "defense_deadline" in row.keys() and row["defense_deadline"]
                else None
            ),
            outcome=(str(row["outcome"]) if "outcome" in row.keys() and row["outcome"] else None),
            summons_message_id=(
                int(row["summons_message_id"])
                if "summons_message_id" in row.keys() and row["summons_message_id"] is not None
                else None
            ),
        )

    def impose_sanction(
        self,
        source_state: int,
        target_state: int,
        reason: str,
        *,
        duration: timedelta = SANCTION_DURATION,
    ) -> tuple[bool, WorldSanction | None]:
        clean_reason = " ".join((reason or "").split())[:300]
        now = datetime.now(timezone.utc)
        try:
            with self._connect() as conn:
                # An expired package must not block a fresh one via the unique index.
                conn.execute(
                    """
                    UPDATE world_sanctions SET lifted_at = expires_at
                    WHERE source_state = ? AND target_state = ? AND lifted_at IS NULL
                      AND expires_at IS NOT NULL AND expires_at <= ?
                    """,
                    (source_state, target_state, now.isoformat()),
                )
                cursor = conn.execute(
                    """
                    INSERT INTO world_sanctions(source_state, target_state, reason, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (source_state, target_state, clean_reason, now.isoformat(), (now + duration).isoformat()),
                )
                row = conn.execute("SELECT * FROM world_sanctions WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return True, self._sanction(row) if row is not None else None
        except sqlite3.IntegrityError:
            return False, None

    def expire_sanctions(self, now: datetime | None = None) -> list[WorldSanction]:
        """Close packages whose week is over and return them for the chronicle."""
        current = (now or datetime.now(timezone.utc)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM world_sanctions
                WHERE lifted_at IS NULL AND expires_at IS NOT NULL AND expires_at <= ?
                """,
                (current,),
            ).fetchall()
            if rows:
                conn.execute(
                    """
                    UPDATE world_sanctions SET lifted_at = expires_at
                    WHERE lifted_at IS NULL AND expires_at IS NOT NULL AND expires_at <= ?
                    """,
                    (current,),
                )
        return [self._sanction(row) for row in rows]

    def has_active_sanction(self, source_state: int, target_state: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM world_sanctions
                WHERE source_state = ? AND target_state = ? AND lifted_at IS NULL
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (source_state, target_state, datetime.now(timezone.utc).isoformat()),
            ).fetchone()
        return row is not None

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
        params: tuple[object, ...] = (datetime.now(timezone.utc).isoformat(),)
        where = "WHERE lifted_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"
        if world_id is not None:
            where += " AND (source_state = ? OR target_state = ?)"
            params = (*params, world_id, world_id)
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

    def open_case(
        self,
        plaintiff_state: int,
        defendant_state: int,
        claim: str,
        defense_deadline: datetime,
    ) -> tuple[str, WorldCourtCase | None]:
        """Register a claim that waits for the defendant's position."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute(
                """
                SELECT * FROM world_court_cases
                WHERE status = 'awaiting_defense'
                  AND MIN(plaintiff_state, defendant_state) = MIN(?, ?)
                  AND MAX(plaintiff_state, defendant_state) = MAX(?, ?)
                ORDER BY id DESC LIMIT 1
                """,
                (plaintiff_state, defendant_state, plaintiff_state, defendant_state),
            ).fetchone()
            if pending is not None:
                return "pending_exists", self._case(pending)
            cursor = conn.execute(
                """
                INSERT INTO world_court_cases(
                    plaintiff_state, defendant_state, claim, verdict, created_at, status, defense_deadline
                ) VALUES (?, ?, ?, '', ?, 'awaiting_defense', ?)
                """,
                (plaintiff_state, defendant_state, claim[:1000], self._now(), defense_deadline.isoformat()),
            )
            row = conn.execute("SELECT * FROM world_court_cases WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return "opened", self._case(row)

    def get_case(self, case_id: int) -> WorldCourtCase | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM world_court_cases WHERE id = ?", (int(case_id),)).fetchone()
        return self._case(row) if row is not None else None

    def set_summons(self, case_id: int, message_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE world_court_cases SET summons_message_id = ? WHERE id = ?",
                (int(message_id), int(case_id)),
            )

    def get_case_by_summons(self, defendant_state: int, message_id: int) -> WorldCourtCase | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM world_court_cases
                WHERE defendant_state = ? AND summons_message_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (int(defendant_state), int(message_id)),
            ).fetchone()
        return self._case(row) if row is not None else None

    def add_defense(self, case_id: int, statement: str, *, limit: int = 3000) -> bool:
        clean = " ".join((statement or "").split()).strip()
        if not clean:
            return False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT defense, status FROM world_court_cases WHERE id = ?", (int(case_id),)
            ).fetchone()
            if row is None or str(row["status"]) != "awaiting_defense":
                return False
            current = str(row["defense"] or "")
            combined = f"{current}\n{clean}".strip() if current else clean
            if len(combined) > limit:
                return False
            conn.execute(
                "UPDATE world_court_cases SET defense = ? WHERE id = ?", (combined, int(case_id))
            )
        return True

    def list_due_cases(self, now: datetime) -> list[WorldCourtCase]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM world_court_cases
                WHERE status = 'awaiting_defense' AND defense_deadline <= ?
                ORDER BY defense_deadline
                """,
                (now.isoformat(),),
            ).fetchall()
        return [self._case(row) for row in rows]

    def claim_for_deliberation(self, case_id: int) -> WorldCourtCase | None:
        """Move a pending case to deliberation exactly once (manual button vs loop)."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE world_court_cases SET status = 'deliberating'
                WHERE id = ? AND status = 'awaiting_defense'
                """,
                (int(case_id),),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM world_court_cases WHERE id = ?", (int(case_id),)).fetchone()
        return self._case(row)

    def release_deliberation(self, case_id: int) -> None:
        """Return a case to the queue when the model failed, so the loop retries."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE world_court_cases SET status = 'awaiting_defense' WHERE id = ? AND status = 'deliberating'",
                (int(case_id),),
            )

    def reset_stuck_deliberations(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE world_court_cases SET status = 'awaiting_defense' WHERE status = 'deliberating'"
            )

    def decide_case(self, case_id: int, verdict: str, outcome: str | None) -> WorldCourtCase | None:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE world_court_cases SET verdict = ?, outcome = ?, status = 'decided'
                WHERE id = ? AND status IN ('awaiting_defense', 'deliberating')
                """,
                (verdict[:6000], outcome, int(case_id)),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM world_court_cases WHERE id = ?", (int(case_id),)).fetchone()
        return self._case(row)

    def latest_case_filed_by(self, plaintiff_state: int) -> WorldCourtCase | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM world_court_cases WHERE plaintiff_state = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (int(plaintiff_state),),
            ).fetchone()
        return self._case(row) if row is not None else None

    def list_court_cases(self, *, limit: int = 10) -> list[WorldCourtCase]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_court_cases ORDER BY created_at DESC, id DESC LIMIT ?",
                (max(1, min(int(limit), 50)),),
            ).fetchall()
        return [self._case(row) for row in rows]
