"""Persistence for World of Upupa blocs: multilateral alliances with voted admission."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3


MAX_BLOC_MEMBERS = 8


@dataclass(frozen=True)
class WorldBloc:
    bloc_id: int
    name: str
    founder_state: int
    created_at: datetime
    members: tuple[int, ...]


@dataclass(frozen=True)
class BlocApplication:
    application_id: int
    bloc_id: int
    world_id: int
    status: str
    needed: int
    electorate: tuple[int, ...]
    created_at: datetime
    expires_at: datetime
    yes: int = 0
    no: int = 0


def _dt(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def votes_needed(members: int) -> int:
    """Simple majority of the current members; a lone founder decides alone."""
    return max(1, math.ceil(max(1, int(members)) / 2))


class SQLiteWorldBlocRepository:
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

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_blocs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    founder_state INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    dissolved_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_world_blocs_name
                ON world_blocs(name COLLATE NOCASE) WHERE dissolved_at IS NULL
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_bloc_members (
                    world_id INTEGER PRIMARY KEY,
                    bloc_id INTEGER NOT NULL,
                    joined_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_bloc_members_bloc ON world_bloc_members(bloc_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_bloc_applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bloc_id INTEGER NOT NULL,
                    world_id INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'accepted', 'rejected', 'expired')),
                    needed INTEGER NOT NULL,
                    electorate TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_world_bloc_applications_pending
                ON world_bloc_applications(bloc_id, world_id) WHERE status = 'pending'
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_bloc_votes (
                    application_id INTEGER NOT NULL,
                    voter_state INTEGER NOT NULL,
                    vote INTEGER NOT NULL CHECK(vote IN (0, 1)),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (application_id, voter_state)
                )
                """
            )

    # ------------------------------------------------------------------ blocs

    def _members(self, conn: sqlite3.Connection, bloc_id: int) -> tuple[int, ...]:
        rows = conn.execute(
            "SELECT world_id FROM world_bloc_members WHERE bloc_id = ? ORDER BY joined_at, world_id",
            (int(bloc_id),),
        ).fetchall()
        return tuple(int(row["world_id"]) for row in rows)

    def _bloc(self, conn: sqlite3.Connection, row: sqlite3.Row) -> WorldBloc:
        return WorldBloc(
            bloc_id=int(row["id"]),
            name=str(row["name"]),
            founder_state=int(row["founder_state"]),
            created_at=_dt(row["created_at"]),
            members=self._members(conn, int(row["id"])),
        )

    def create_bloc(self, founder_state: int, name: str) -> tuple[str, WorldBloc | None]:
        clean = " ".join((name or "").split()).strip()[:40]
        if not clean:
            return "empty", None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            member = conn.execute(
                "SELECT bloc_id FROM world_bloc_members WHERE world_id = ?", (int(founder_state),)
            ).fetchone()
            if member is not None:
                return "already_member", None
            # SQLite NOCASE folds only ASCII; compare Cyrillic names in Python.
            active = conn.execute("SELECT name FROM world_blocs WHERE dissolved_at IS NULL").fetchall()
            if any(str(row["name"]).casefold() == clean.casefold() for row in active):
                return "name_taken", None
            now = self._now()
            cursor = conn.execute(
                "INSERT INTO world_blocs(name, founder_state, created_at) VALUES (?, ?, ?)",
                (clean, int(founder_state), now),
            )
            bloc_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO world_bloc_members(world_id, bloc_id, joined_at) VALUES (?, ?, ?)",
                (int(founder_state), bloc_id, now),
            )
            row = conn.execute("SELECT * FROM world_blocs WHERE id = ?", (bloc_id,)).fetchone()
            return "created", self._bloc(conn, row)

    def get_bloc(self, bloc_id: int) -> WorldBloc | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_blocs WHERE id = ? AND dissolved_at IS NULL", (int(bloc_id),)
            ).fetchone()
            return self._bloc(conn, row) if row is not None else None

    def get_bloc_of(self, world_id: int) -> WorldBloc | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT b.* FROM world_bloc_members m JOIN world_blocs b ON b.id = m.bloc_id
                WHERE m.world_id = ? AND b.dissolved_at IS NULL
                """,
                (int(world_id),),
            ).fetchone()
            return self._bloc(conn, row) if row is not None else None

    def list_blocs(self) -> list[WorldBloc]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_blocs WHERE dissolved_at IS NULL ORDER BY created_at, id"
            ).fetchall()
            return [self._bloc(conn, row) for row in rows]

    def bloc_sizes(self) -> dict[int, int]:
        """world_id -> number of members in that state's bloc (including itself)."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.world_id, (
                    SELECT COUNT(*) FROM world_bloc_members other WHERE other.bloc_id = m.bloc_id
                ) AS size
                FROM world_bloc_members m JOIN world_blocs b ON b.id = m.bloc_id
                WHERE b.dissolved_at IS NULL
                """
            ).fetchall()
        return {int(row["world_id"]): int(row["size"]) for row in rows}

    def leave_bloc(self, world_id: int) -> tuple[str, WorldBloc | None]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT b.* FROM world_bloc_members m JOIN world_blocs b ON b.id = m.bloc_id
                WHERE m.world_id = ? AND b.dissolved_at IS NULL
                """,
                (int(world_id),),
            ).fetchone()
            if row is None:
                return "not_member", None
            bloc = self._bloc(conn, row)
            conn.execute("DELETE FROM world_bloc_members WHERE world_id = ?", (int(world_id),))
            remaining = self._members(conn, bloc.bloc_id)
            if not remaining:
                conn.execute(
                    "UPDATE world_blocs SET dissolved_at = ? WHERE id = ?", (self._now(), bloc.bloc_id)
                )
                conn.execute(
                    "UPDATE world_bloc_applications SET status = 'expired' WHERE bloc_id = ? AND status = 'pending'",
                    (bloc.bloc_id,),
                )
                return "dissolved", bloc
            return "left", bloc

    def remove_state_everywhere(self, world_id: int) -> None:
        """A state leaving the World also leaves its bloc."""
        self.leave_bloc(world_id)

    # ----------------------------------------------------------- applications

    def _application(self, conn: sqlite3.Connection, row: sqlite3.Row) -> BlocApplication:
        votes = conn.execute(
            "SELECT vote, COUNT(*) AS n FROM world_bloc_votes WHERE application_id = ? GROUP BY vote",
            (int(row["id"]),),
        ).fetchall()
        tally = {int(item["vote"]): int(item["n"]) for item in votes}
        electorate = tuple(int(item) for item in str(row["electorate"]).split(",") if item)
        return BlocApplication(
            application_id=int(row["id"]),
            bloc_id=int(row["bloc_id"]),
            world_id=int(row["world_id"]),
            status=str(row["status"]),
            needed=int(row["needed"]),
            electorate=electorate,
            created_at=_dt(row["created_at"]),
            expires_at=_dt(row["expires_at"]),
            yes=tally.get(1, 0),
            no=tally.get(0, 0),
        )

    def apply(self, bloc_id: int, world_id: int, *, expires_at: datetime) -> tuple[str, BlocApplication | None]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            bloc_row = conn.execute(
                "SELECT * FROM world_blocs WHERE id = ? AND dissolved_at IS NULL", (int(bloc_id),)
            ).fetchone()
            if bloc_row is None:
                return "unknown_bloc", None
            if conn.execute(
                "SELECT 1 FROM world_bloc_members WHERE world_id = ?", (int(world_id),)
            ).fetchone() is not None:
                return "already_member", None
            members = self._members(conn, int(bloc_id))
            if len(members) >= MAX_BLOC_MEMBERS:
                return "full", None
            pending = conn.execute(
                "SELECT * FROM world_bloc_applications WHERE bloc_id = ? AND world_id = ? AND status = 'pending'",
                (int(bloc_id), int(world_id)),
            ).fetchone()
            if pending is not None:
                return "duplicate", self._application(conn, pending)
            cursor = conn.execute(
                """
                INSERT INTO world_bloc_applications(bloc_id, world_id, status, needed, electorate, created_at, expires_at)
                VALUES (?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    int(bloc_id),
                    int(world_id),
                    votes_needed(len(members)),
                    ",".join(str(item) for item in members),
                    self._now(),
                    expires_at.isoformat(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM world_bloc_applications WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return "created", self._application(conn, row)

    def get_application(self, application_id: int) -> BlocApplication | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_bloc_applications WHERE id = ?", (int(application_id),)
            ).fetchone()
            return self._application(conn, row) if row is not None else None

    def vote(self, application_id: int, voter_state: int, approve: bool) -> tuple[str, BlocApplication | None]:
        """Record a member's vote and settle the application once the outcome is certain."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM world_bloc_applications WHERE id = ?", (int(application_id),)
            ).fetchone()
            if row is None:
                return "not_found", None
            application = self._application(conn, row)
            if application.status != "pending":
                return "already_resolved", application
            if int(voter_state) not in application.electorate:
                return "not_voter", application
            if conn.execute(
                "SELECT 1 FROM world_bloc_members WHERE world_id = ? AND bloc_id = ?",
                (int(voter_state), application.bloc_id),
            ).fetchone() is None:
                return "not_voter", application
            conn.execute(
                """
                INSERT INTO world_bloc_votes(application_id, voter_state, vote, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(application_id, voter_state) DO UPDATE SET vote = excluded.vote
                """,
                (int(application_id), int(voter_state), 1 if approve else 0, self._now()),
            )
            application = self._application(conn, row)
            status = "voted"
            if application.yes >= application.needed:
                bloc = conn.execute(
                    "SELECT 1 FROM world_blocs WHERE id = ? AND dissolved_at IS NULL", (application.bloc_id,)
                ).fetchone()
                already = conn.execute(
                    "SELECT 1 FROM world_bloc_members WHERE world_id = ?", (application.world_id,)
                ).fetchone()
                full = len(self._members(conn, application.bloc_id)) >= MAX_BLOC_MEMBERS
                if bloc is None or already is not None or full:
                    conn.execute(
                        "UPDATE world_bloc_applications SET status = 'expired' WHERE id = ?",
                        (int(application_id),),
                    )
                    status = "expired"
                else:
                    conn.execute(
                        "INSERT INTO world_bloc_members(world_id, bloc_id, joined_at) VALUES (?, ?, ?)",
                        (application.world_id, application.bloc_id, self._now()),
                    )
                    conn.execute(
                        "UPDATE world_bloc_applications SET status = 'accepted' WHERE id = ?",
                        (int(application_id),),
                    )
                    status = "accepted"
            elif application.no > len(application.electorate) - application.needed:
                conn.execute(
                    "UPDATE world_bloc_applications SET status = 'rejected' WHERE id = ?",
                    (int(application_id),),
                )
                status = "rejected"
            updated = conn.execute(
                "SELECT * FROM world_bloc_applications WHERE id = ?", (int(application_id),)
            ).fetchone()
            return status, self._application(conn, updated)

    def expire_applications(self, now: datetime) -> list[BlocApplication]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_bloc_applications WHERE status = 'pending' AND expires_at <= ?",
                (now.isoformat(),),
            ).fetchall()
            expired = [self._application(conn, row) for row in rows]
            if rows:
                conn.execute(
                    "UPDATE world_bloc_applications SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?",
                    (now.isoformat(),),
                )
        return expired
