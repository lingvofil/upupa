"""Persistent metadata, event journal and projections for the World of Upupa.

The diplomatic repository keeps the current relations.  This module owns the
rest of the World database:

* ``world_details`` — ambassador, cached chat-derived identity and per-state
  privacy/newsletter settings;
* ``world_events`` — the append-only public chronicle (plus a few internal
  idempotency markers);
* ``world_visits`` / ``world_feedback_windows`` — indexed projections of the
  visit events.  They are maintained in the same transaction as the event that
  changes them, so the 60-second maintenance loop never scans the journal.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from features.world.models import WorldState


VISIT_DURATION = timedelta(hours=24)
PLACEHOLDER_GOVERNMENT = "ещё не сформировался"
PLACEHOLDER_CLIMATE = "недостаточно наблюдений"
PLACEHOLDER_THREAT = "статистическая неопределённость"

# Only used by the one-time migration that retires the old random templates.
_LEGACY_TEMPLATE_THREATS = (
    "понедельник",
    "внезапный рабочий созвон",
    "исчезновение последнего админа",
    "голосовые по семь минут",
    "фраза «есть минутка?»",
    "слишком серьёзный разговор",
    "массовый уход читать, но не отвечать",
    "неожиданная трезвость населения",
    "спор, начавшийся со слова «вообще-то»",
    "человек, который решил всё организовать",
    "дефицит мемов стратегического назначения",
    "сообщение «доброе утро» в 06:12",
)

# Internal bookkeeping that never belongs in the public chronicle.
INTERNAL_EVENT_TYPES = frozenset(
    {
        "state_visit_notification_delivered",
        "state_visit_delivery_completed",
        "state_visit_report",
        "state_visit_feedback",
        "state_visit_feedback_opened",
        "state_visit_feedback_report",
        "state_visit_feedback_closed",
        "state_identity_analyzed",
        "state_identity_analysis_failed",
        "world_newspaper_issued",
    }
)

_SETTING_COLUMNS = {
    "share_identity": "share_identity",
    "share_activity": "share_activity",
    "newspaper": "newspaper",
}


@dataclass(frozen=True)
class WorldDetails:
    world_id: int
    government_form: str
    climate: str
    main_threat: str
    ambassador_user_id: int | None = None
    ambassador_name: str | None = None
    share_identity: bool = True
    share_activity: bool = False
    newspaper: bool = True


@dataclass(frozen=True)
class IdentityCache:
    payload: dict[str, object]
    analyzed_at: datetime
    source: str


@dataclass(frozen=True)
class WorldEvent:
    event_id: int
    event_type: str
    actor_state: int | None
    target_state: int | None
    payload: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class WorldRelation:
    state_a: int
    state_b: int
    relation: str
    alliance_name: str | None = None
    since: datetime | None = None


@dataclass(frozen=True)
class VisitRecord:
    accepted_event_id: int
    host_state: int
    guest_state: int
    accepted_at: datetime
    expires_at: datetime
    finished_at: datetime | None = None
    finish_reason: str | None = None
    delivered_at: datetime | None = None


@dataclass(frozen=True)
class FeedbackWindowRecord:
    accepted_event_id: int
    host_state: int
    guest_state: int
    prompt_message_id: int
    opened_at: datetime
    closes_at: datetime
    closed_at: datetime | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _parse_dt(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return _utc(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _payload(raw: object) -> dict[str, object]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


class WorldLedger:
    """SQLite-backed history/metadata layer sharing the World database file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One short-lived connection per operation: committed and always closed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------ schema

    @staticmethod
    def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_details (
                    world_id INTEGER PRIMARY KEY,
                    government_form TEXT NOT NULL,
                    climate TEXT NOT NULL,
                    main_threat TEXT NOT NULL,
                    ambassador_user_id BIGINT,
                    ambassador_name TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(world_id) REFERENCES world_states(world_id)
                )
                """
            )
            columns = self._columns(conn, "world_details")
            for name, ddl in (
                ("identity_json", "TEXT"),
                ("identity_analyzed_at", "TEXT"),
                ("identity_source", "TEXT"),
                ("share_identity", "INTEGER NOT NULL DEFAULT 1"),
                ("share_activity", "INTEGER NOT NULL DEFAULT 0"),
                ("newspaper", "INTEGER NOT NULL DEFAULT 1"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE world_details ADD COLUMN {name} {ddl}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_alliance_meta (
                    state_a INTEGER NOT NULL,
                    state_b INTEGER NOT NULL,
                    name TEXT NOT NULL,
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
                CREATE TABLE IF NOT EXISTS world_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    actor_state INTEGER,
                    target_state INTEGER,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    dedupe_key TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(actor_state) REFERENCES world_states(world_id),
                    FOREIGN KEY(target_state) REFERENCES world_states(world_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_events_created ON world_events(created_at DESC, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_events_actor ON world_events(actor_state, created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_events_target ON world_events(target_state, created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_events_type ON world_events(event_type, created_at DESC)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_visits (
                    accepted_event_id INTEGER PRIMARY KEY,
                    host_state INTEGER NOT NULL,
                    guest_state INTEGER NOT NULL,
                    accepted_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    finished_at TEXT,
                    finish_reason TEXT,
                    delivered_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_visits_open ON world_visits(finished_at, expires_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_visits_pair ON world_visits(host_state, guest_state, accepted_at DESC)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_feedback_windows (
                    accepted_event_id INTEGER PRIMARY KEY,
                    host_state INTEGER NOT NULL,
                    guest_state INTEGER NOT NULL,
                    prompt_message_id INTEGER NOT NULL,
                    opened_at TEXT NOT NULL,
                    closes_at TEXT NOT NULL,
                    closed_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_feedback_due ON world_feedback_windows(closed_at, closes_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_feedback_prompt ON world_feedback_windows(guest_state, prompt_message_id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS world_visit_ratings (
                    accepted_event_id INTEGER NOT NULL,
                    host_state INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (accepted_event_id, user_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_world_visit_ratings_host ON world_visit_ratings(host_state, created_at)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS world_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self._migrate(conn)

    def _migration_done(self, conn: sqlite3.Connection, key: str) -> bool:
        return conn.execute("SELECT 1 FROM world_meta WHERE key = ?", (key,)).fetchone() is not None

    def _mark_migration(self, conn: sqlite3.Connection, key: str) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO world_meta(key, value) VALUES (?, ?)", (key, self._now())
        )

    def _migrate(self, conn: sqlite3.Connection) -> None:
        now = datetime.now(timezone.utc)
        if not self._migration_done(conn, "retire_template_identity_v1"):
            # Old details were random templates; real traits come from chat analysis.
            placeholders = ",".join("?" for _ in _LEGACY_TEMPLATE_THREATS)
            conn.execute(
                f"""
                UPDATE world_details
                SET government_form = ?, climate = ?, main_threat = ?
                WHERE main_threat IN ({placeholders})
                """,
                (PLACEHOLDER_GOVERNMENT, PLACEHOLDER_CLIMATE, PLACEHOLDER_THREAT, *_LEGACY_TEMPLATE_THREATS),
            )
            self._backfill_identity(conn)
            self._mark_migration(conn, "retire_template_identity_v1")
        if not self._migration_done(conn, "visit_projections_v1"):
            self._backfill_visits(conn, now)
            self._backfill_feedback_windows(conn, now)
            self._mark_migration(conn, "visit_projections_v1")

    def _backfill_identity(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            SELECT e.actor_state, e.payload_json, e.created_at FROM world_events e
            WHERE e.event_type = 'state_identity_analyzed' AND e.actor_state IS NOT NULL
            ORDER BY e.created_at ASC, e.id ASC
            """
        ).fetchall()
        latest: dict[int, sqlite3.Row] = {}
        for row in rows:
            latest[int(row["actor_state"])] = row
        for world_id, row in latest.items():
            payload = _payload(row["payload_json"])
            self._store_identity(
                conn,
                world_id,
                payload,
                analyzed_at=_parse_dt(row["created_at"]) or datetime.now(timezone.utc),
                source=str(payload.get("source") or "analysis"),
            )

    def _backfill_visits(self, conn: sqlite3.Connection, now: datetime) -> None:
        accepted = conn.execute(
            """
            SELECT id, actor_state, target_state, created_at FROM world_events
            WHERE event_type = 'state_visit_accepted'
              AND actor_state IS NOT NULL AND target_state IS NOT NULL
            ORDER BY id
            """
        ).fetchall()
        for row in accepted:
            accepted_at = _parse_dt(row["created_at"]) or now
            conn.execute(
                """
                INSERT OR IGNORE INTO world_visits(
                    accepted_event_id, host_state, guest_state, accepted_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(row["id"]),
                    int(row["target_state"]),
                    int(row["actor_state"]),
                    _iso(accepted_at),
                    _iso(accepted_at + VISIT_DURATION),
                ),
            )
        finished = conn.execute(
            """
            SELECT id, actor_state, target_state, payload_json, created_at FROM world_events
            WHERE event_type = 'state_visit_finished'
            ORDER BY id
            """
        ).fetchall()
        for row in finished:
            payload = _payload(row["payload_json"])
            reason = str(payload.get("reason") or "manual")
            finished_at = row["created_at"]
            accepted_id = payload.get("accepted_event_id")
            if accepted_id is not None:
                conn.execute(
                    """
                    UPDATE world_visits SET finished_at = ?, finish_reason = ?
                    WHERE accepted_event_id = ? AND finished_at IS NULL
                    """,
                    (finished_at, reason, int(accepted_id)),
                )
            else:
                conn.execute(
                    """
                    UPDATE world_visits SET finished_at = ?, finish_reason = ?
                    WHERE host_state = ? AND guest_state = ? AND finished_at IS NULL
                      AND accepted_at <= ?
                    """,
                    (finished_at, reason, row["actor_state"], row["target_state"], finished_at),
                )
        done = conn.execute(
            """
            SELECT dedupe_key, created_at FROM world_events
            WHERE event_type = 'state_visit_delivery_completed' AND dedupe_key IS NOT NULL
            """
        ).fetchall()
        for row in done:
            key = str(row["dedupe_key"])
            try:
                accepted_id = int(key.rsplit(":", 1)[1])
            except (IndexError, ValueError):
                continue
            conn.execute(
                "UPDATE world_visits SET delivered_at = ? WHERE accepted_event_id = ?",
                (row["created_at"], accepted_id),
            )
        # Visits finished before versioned delivery existed were never re-sent;
        # keep it that way instead of flooding chats with ancient reports.
        conn.execute(
            """
            UPDATE world_visits SET delivered_at = finished_at
            WHERE finished_at IS NOT NULL AND delivered_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM world_events f
                  WHERE f.event_type = 'state_visit_finished'
                    AND json_extract(f.payload_json, '$.delivery_version') = 1
                    AND json_extract(f.payload_json, '$.accepted_event_id') = world_visits.accepted_event_id
              )
            """
        )
        # A visit that expired more than a day ago would already have been closed by
        # the live loop; anything still open is a lost legacy visit — retire it quietly.
        stale = _iso(now - timedelta(days=1))
        conn.execute(
            """
            UPDATE world_visits
            SET finished_at = expires_at, finish_reason = 'legacy', delivered_at = expires_at
            WHERE finished_at IS NULL AND expires_at < ?
            """,
            (stale,),
        )

    def _backfill_feedback_windows(self, conn: sqlite3.Connection, now: datetime) -> None:
        rows = conn.execute(
            """
            SELECT actor_state, target_state, payload_json, created_at FROM world_events
            WHERE event_type = 'state_visit_feedback_opened'
            ORDER BY id
            """
        ).fetchall()
        for row in rows:
            payload = _payload(row["payload_json"])
            try:
                accepted_id = int(payload["accepted_event_id"])
                prompt_id = int(payload["prompt_message_id"])
                closes_at = str(payload["closes_at"])
            except (KeyError, TypeError, ValueError):
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO world_feedback_windows(
                    accepted_event_id, host_state, guest_state, prompt_message_id, opened_at, closes_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (accepted_id, row["target_state"], row["actor_state"], prompt_id, row["created_at"], closes_at),
            )
        closed = conn.execute(
            """
            SELECT payload_json, created_at FROM world_events
            WHERE event_type = 'state_visit_feedback_closed'
            """
        ).fetchall()
        for row in closed:
            accepted_id = _payload(row["payload_json"]).get("accepted_event_id")
            if accepted_id is None:
                continue
            conn.execute(
                "UPDATE world_feedback_windows SET closed_at = ? WHERE accepted_event_id = ?",
                (row["created_at"], int(accepted_id)),
            )
        conn.execute(
            """
            UPDATE world_feedback_windows SET closed_at = closes_at
            WHERE closed_at IS NULL AND closes_at < ?
            """,
            (_iso(now - timedelta(days=1)),),
        )

    # ----------------------------------------------------------------- details

    @staticmethod
    def _details(row: sqlite3.Row | None) -> WorldDetails | None:
        if row is None:
            return None
        keys = row.keys()
        return WorldDetails(
            world_id=int(row["world_id"]),
            government_form=str(row["government_form"]),
            climate=str(row["climate"]),
            main_threat=str(row["main_threat"]),
            ambassador_user_id=(int(row["ambassador_user_id"]) if row["ambassador_user_id"] is not None else None),
            ambassador_name=(str(row["ambassador_name"]) if row["ambassador_name"] else None),
            share_identity=bool(row["share_identity"]) if "share_identity" in keys else True,
            share_activity=bool(row["share_activity"]) if "share_activity" in keys else False,
            newspaper=bool(row["newspaper"]) if "newspaper" in keys else True,
        )

    def ensure_details(self, state: WorldState) -> WorldDetails:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_details WHERE world_id = ?", (state.world_id,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO world_details(
                        world_id, government_form, climate, main_threat, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        state.world_id,
                        PLACEHOLDER_GOVERNMENT,
                        PLACEHOLDER_CLIMATE,
                        PLACEHOLDER_THREAT,
                        self._now(),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM world_details WHERE world_id = ?", (state.world_id,)
                ).fetchone()
        details = self._details(row)
        if details is None:  # pragma: no cover - defensive guard
            raise RuntimeError(f"Failed to create world details for state {state.world_id}")
        return details

    def get_details(self, world_id: int) -> WorldDetails | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_details WHERE world_id = ?",
                (world_id,),
            ).fetchone()
        return self._details(row)

    def list_details(self) -> dict[int, WorldDetails]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM world_details").fetchall()
        result = {}
        for row in rows:
            details = self._details(row)
            if details is not None:
                result[details.world_id] = details
        return result

    def set_ambassador(self, world_id: int, user_id: int | None, name: str | None) -> WorldDetails:
        now = self._now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE world_details
                SET ambassador_user_id = ?, ambassador_name = ?, updated_at = ?
                WHERE world_id = ?
                """,
                (user_id, name, now, world_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"World details not found for state {world_id}")
            row = conn.execute(
                "SELECT * FROM world_details WHERE world_id = ?",
                (world_id,),
            ).fetchone()
        details = self._details(row)
        if details is None:  # pragma: no cover
            raise RuntimeError("Failed to read updated ambassador")
        return details

    def set_setting(self, world_id: int, key: str, value: bool) -> WorldDetails:
        column = _SETTING_COLUMNS.get(key)
        if column is None:
            raise ValueError(f"Unknown world setting: {key}")
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE world_details SET {column} = ?, updated_at = ? WHERE world_id = ?",
                (1 if value else 0, self._now(), world_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"World details not found for state {world_id}")
            row = conn.execute("SELECT * FROM world_details WHERE world_id = ?", (world_id,)).fetchone()
        details = self._details(row)
        if details is None:  # pragma: no cover
            raise RuntimeError("Failed to read updated settings")
        return details

    @staticmethod
    def _store_identity(
        conn: sqlite3.Connection,
        world_id: int,
        payload: dict[str, object],
        *,
        analyzed_at: datetime,
        source: str,
    ) -> None:
        conn.execute(
            """
            UPDATE world_details
            SET identity_json = ?, identity_analyzed_at = ?, identity_source = ?
            WHERE world_id = ?
            """,
            (
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                _iso(analyzed_at),
                source,
                world_id,
            ),
        )
        if source == "analysis":
            government = str(payload.get("government_form") or "").strip()
            climate = str(payload.get("climate") or "").strip()
            threat = str(payload.get("main_threat") or "").strip()
            if government and climate and threat:
                conn.execute(
                    """
                    UPDATE world_details SET government_form = ?, climate = ?, main_threat = ?
                    WHERE world_id = ?
                    """,
                    (government, climate, threat, world_id),
                )

    def store_identity(
        self,
        world_id: int,
        payload: dict[str, object],
        *,
        analyzed_at: datetime,
        source: str,
    ) -> None:
        with self._connect() as conn:
            self._store_identity(conn, world_id, payload, analyzed_at=analyzed_at, source=source)

    def get_identity_cache(self, world_id: int) -> IdentityCache | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT identity_json, identity_analyzed_at, identity_source
                FROM world_details WHERE world_id = ?
                """,
                (world_id,),
            ).fetchone()
        if row is None or not row["identity_analyzed_at"]:
            return None
        analyzed_at = _parse_dt(row["identity_analyzed_at"])
        if analyzed_at is None:
            return None
        return IdentityCache(
            payload=_payload(row["identity_json"]),
            analyzed_at=analyzed_at,
            source=str(row["identity_source"] or "analysis"),
        )

    # --------------------------------------------------------------- alliances

    @staticmethod
    def _pair(state_a: int, state_b: int) -> tuple[int, int]:
        return tuple(sorted((int(state_a), int(state_b))))  # type: ignore[return-value]

    def get_alliance_name(self, state_a: int, state_b: int) -> str | None:
        first, second = self._pair(state_a, state_b)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name FROM world_alliance_meta WHERE state_a = ? AND state_b = ?",
                (first, second),
            ).fetchone()
        return str(row["name"]) if row is not None else None

    def get_alliance_names(self, world_id: int) -> dict[int, str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT state_a, state_b, name FROM world_alliance_meta WHERE state_a = ? OR state_b = ?",
                (world_id, world_id),
            ).fetchall()
        result = {}
        for row in rows:
            other = int(row["state_b"]) if int(row["state_a"]) == int(world_id) else int(row["state_a"])
            result[other] = str(row["name"])
        return result

    def set_alliance_name(self, state_a: int, state_b: int, name: str) -> None:
        first, second = self._pair(state_a, state_b)
        clean = " ".join(name.split()).strip()[:80]
        if not clean:
            raise ValueError("Alliance name cannot be empty")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO world_alliance_meta(state_a, state_b, name, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(state_a, state_b)
                DO UPDATE SET name = excluded.name, updated_at = excluded.updated_at
                """,
                (first, second, clean, self._now()),
            )

    def clear_alliance_name(self, state_a: int, state_b: int) -> None:
        first, second = self._pair(state_a, state_b)
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM world_alliance_meta WHERE state_a = ? AND state_b = ?",
                (first, second),
            )

    # ------------------------------------------------------------------ events

    def _apply_projection(
        self,
        conn: sqlite3.Connection,
        event_id: int,
        event_type: str,
        actor_state: int | None,
        target_state: int | None,
        payload: dict[str, object],
        created_at: datetime,
    ) -> None:
        if event_type == "state_visit_accepted" and actor_state is not None and target_state is not None:
            conn.execute(
                """
                INSERT OR IGNORE INTO world_visits(
                    accepted_event_id, host_state, guest_state, accepted_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, target_state, actor_state, _iso(created_at), _iso(created_at + VISIT_DURATION)),
            )
        elif event_type == "state_visit_finished" and payload.get("accepted_event_id") is not None:
            conn.execute(
                """
                UPDATE world_visits SET finished_at = ?, finish_reason = ?
                WHERE accepted_event_id = ? AND finished_at IS NULL
                """,
                (_iso(created_at), str(payload.get("reason") or "manual"), int(payload["accepted_event_id"])),
            )
        elif event_type == "state_visit_delivery_completed" and payload.get("accepted_event_id") is not None:
            conn.execute(
                "UPDATE world_visits SET delivered_at = ? WHERE accepted_event_id = ?",
                (_iso(created_at), int(payload["accepted_event_id"])),
            )
        elif event_type == "state_visit_feedback_opened" and actor_state is not None and target_state is not None:
            conn.execute(
                """
                INSERT OR IGNORE INTO world_feedback_windows(
                    accepted_event_id, host_state, guest_state, prompt_message_id, opened_at, closes_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(payload["accepted_event_id"]),
                    target_state,
                    actor_state,
                    int(payload["prompt_message_id"]),
                    _iso(created_at),
                    str(payload["closes_at"]),
                ),
            )
        elif event_type == "state_visit_feedback_closed" and payload.get("accepted_event_id") is not None:
            conn.execute(
                "UPDATE world_feedback_windows SET closed_at = ? WHERE accepted_event_id = ?",
                (_iso(created_at), int(payload["accepted_event_id"])),
            )

    def record_event(
        self,
        event_type: str,
        *,
        actor_state: int | None = None,
        target_state: int | None = None,
        payload: dict[str, object] | None = None,
        created_at: datetime | None = None,
        dedupe_key: str | None = None,
    ) -> bool:
        when = created_at or datetime.now(timezone.utc)
        data = payload or {}
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO world_events(
                        event_type, actor_state, target_state, payload_json, dedupe_key, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_type,
                        actor_state,
                        target_state,
                        json.dumps(data, ensure_ascii=False, sort_keys=True),
                        dedupe_key,
                        when.isoformat(),
                    ),
                )
                self._apply_projection(
                    conn,
                    int(cursor.lastrowid),
                    event_type,
                    actor_state,
                    target_state,
                    data,
                    _utc(when),
                )
            return True
        except sqlite3.IntegrityError:
            if dedupe_key:
                return False
            raise

    def ensure_foundation_event(self, state: WorldState) -> None:
        self.record_event(
            "state_founded",
            actor_state=state.world_id,
            payload={"title": state.title},
            created_at=state.created_at,
            dedupe_key=f"state_founded:{state.world_id}",
        )

    @staticmethod
    def _event(row: sqlite3.Row) -> WorldEvent:
        return WorldEvent(
            event_id=int(row["id"]),
            event_type=str(row["event_type"]),
            actor_state=(int(row["actor_state"]) if row["actor_state"] is not None else None),
            target_state=(int(row["target_state"]) if row["target_state"] is not None else None),
            payload=_payload(row["payload_json"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def get_event(self, event_id: int) -> WorldEvent | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM world_events WHERE id = ?", (int(event_id),)).fetchone()
        return self._event(row) if row is not None else None

    def get_event_by_dedupe_key(self, key: str) -> WorldEvent | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM world_events WHERE dedupe_key = ?", (key,)).fetchone()
        return self._event(row) if row is not None else None

    def list_events(
        self,
        *,
        limit: int = 30,
        days: int | None = None,
        world_id: int | None = None,
        event_types: set[str] | None = None,
        actor_only: bool = False,
    ) -> list[WorldEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if days is not None:
            since = datetime.now(timezone.utc) - timedelta(days=max(0, days))
            clauses.append("created_at >= ?")
            params.append(since.isoformat())
        if world_id is not None:
            if actor_only:
                clauses.append("actor_state = ?")
                params.append(world_id)
            else:
                clauses.append("(actor_state = ? OR target_state = ?)")
                params.extend((world_id, world_id))
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(sorted(event_types))
        else:
            placeholders = ",".join("?" for _ in INTERNAL_EVENT_TYPES)
            clauses.append(f"event_type NOT IN ({placeholders})")
            params.extend(sorted(INTERNAL_EVENT_TYPES))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM world_events {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._event(row) for row in rows]

    def list_pair_events(
        self,
        state_a: int,
        state_b: int,
        *,
        days: int = 60,
        limit: int = 20,
        event_types: set[str] | None = None,
    ) -> list[WorldEvent]:
        """Events between exactly these two states (either direction), newest first."""
        since = (datetime.now(timezone.utc) - timedelta(days=max(0, days))).isoformat()
        type_clause = ""
        params: list[object] = [state_a, state_b, state_b, state_a, since]
        if event_types:
            type_clause = f"AND event_type IN ({','.join('?' for _ in event_types)})"
            params.extend(sorted(event_types))
        else:
            type_clause = f"AND event_type NOT IN ({','.join('?' for _ in INTERNAL_EVENT_TYPES)})"
            params.extend(sorted(INTERNAL_EVENT_TYPES))
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM world_events
                WHERE ((actor_state = ? AND target_state = ?) OR (actor_state = ? AND target_state = ?))
                  AND created_at >= ? {type_clause}
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._event(row) for row in rows]

    def latest_event(
        self,
        event_types: set[str],
        *,
        actor_state: int | None = None,
        target_state: int | None = None,
    ) -> WorldEvent | None:
        clauses = [f"event_type IN ({','.join('?' for _ in event_types)})"]
        params: list[object] = sorted(event_types)
        if actor_state is not None:
            clauses.append("actor_state = ?")
            params.append(actor_state)
        if target_state is not None:
            clauses.append("target_state = ?")
            params.append(target_state)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM world_events WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        return self._event(row) if row is not None else None

    def list_relations(self, *, active_only: bool = True) -> list[WorldRelation]:
        enabled_filter = "AND a.enabled = 1 AND b.enabled = 1" if active_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT dr.state_a, dr.state_b, dr.relation, dr.updated_at, wam.name AS alliance_name
                FROM diplomatic_relations dr
                JOIN world_states a ON a.world_id = dr.state_a
                JOIN world_states b ON b.world_id = dr.state_b
                LEFT JOIN world_alliance_meta wam
                  ON wam.state_a = dr.state_a AND wam.state_b = dr.state_b
                WHERE 1 = 1 {enabled_filter}
                ORDER BY dr.state_a, dr.state_b
                """
            ).fetchall()
        return [
            WorldRelation(
                state_a=int(row["state_a"]),
                state_b=int(row["state_b"]),
                relation=str(row["relation"]),
                alliance_name=(str(row["alliance_name"]) if row["alliance_name"] else None),
                since=_parse_dt(row["updated_at"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------ visits

    @staticmethod
    def _visit(row: sqlite3.Row) -> VisitRecord:
        return VisitRecord(
            accepted_event_id=int(row["accepted_event_id"]),
            host_state=int(row["host_state"]),
            guest_state=int(row["guest_state"]),
            accepted_at=_parse_dt(row["accepted_at"]),  # type: ignore[arg-type]
            expires_at=_parse_dt(row["expires_at"]),  # type: ignore[arg-type]
            finished_at=_parse_dt(row["finished_at"]),
            finish_reason=(str(row["finish_reason"]) if row["finish_reason"] else None),
            delivered_at=_parse_dt(row["delivered_at"]),
        )

    def get_open_visit(self, host_state: int, guest_state: int) -> VisitRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM world_visits
                WHERE host_state = ? AND guest_state = ? AND finished_at IS NULL
                ORDER BY accepted_at DESC, accepted_event_id DESC LIMIT 1
                """,
                (host_state, guest_state),
            ).fetchone()
        return self._visit(row) if row is not None else None

    def get_visit(self, accepted_event_id: int) -> VisitRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_visits WHERE accepted_event_id = ?", (int(accepted_event_id),)
            ).fetchone()
        return self._visit(row) if row is not None else None

    def list_expired_open_visits(self, now: datetime) -> list[VisitRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM world_visits
                WHERE finished_at IS NULL AND expires_at <= ?
                ORDER BY expires_at
                """,
                (_iso(now),),
            ).fetchall()
        return [self._visit(row) for row in rows]

    def list_undelivered_finished_visits(self) -> list[VisitRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM world_visits
                WHERE finished_at IS NOT NULL AND delivered_at IS NULL
                ORDER BY finished_at
                """
            ).fetchall()
        return [self._visit(row) for row in rows]

    def pending_visit_deliveries(self) -> list[VisitRecord]:
        """Finished visits whose reports have not reached both states yet."""
        return self.list_undelivered_finished_visits()

    def count_open_visits(self, world_id: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM world_visits
                WHERE finished_at IS NULL AND (host_state = ? OR guest_state = ?)
                """,
                (world_id, world_id),
            ).fetchone()
        return int(row[0])

    def rate_visit(self, accepted_event_id: int, host_state: int, user_id: int, rating: int) -> bool:
        """Store or update one citizen's star rating; returns True for a new vote."""
        rating = max(1, min(5, int(rating)))
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM world_visit_ratings WHERE accepted_event_id = ? AND user_id = ?",
                (accepted_event_id, user_id),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO world_visit_ratings(accepted_event_id, host_state, user_id, rating, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(accepted_event_id, user_id)
                DO UPDATE SET rating = excluded.rating, created_at = excluded.created_at
                """,
                (accepted_event_id, host_state, user_id, rating, self._now()),
            )
        return existing is None

    def visit_rating(self, accepted_event_id: int) -> tuple[float | None, int]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT AVG(rating), COUNT(*) FROM world_visit_ratings WHERE accepted_event_id = ?",
                (accepted_event_id,),
            ).fetchone()
        return (float(row[0]) if row[0] is not None else None), int(row[1])

    def tourism_ratings(self, *, days: int = 30) -> dict[int, tuple[float, int]]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT host_state, AVG(rating) AS avg_rating, COUNT(*) AS votes
                FROM world_visit_ratings WHERE created_at >= ?
                GROUP BY host_state
                """,
                (since,),
            ).fetchall()
        return {int(row["host_state"]): (float(row["avg_rating"]), int(row["votes"])) for row in rows}

    # -------------------------------------------------------- feedback windows

    @staticmethod
    def _window(row: sqlite3.Row) -> FeedbackWindowRecord:
        return FeedbackWindowRecord(
            accepted_event_id=int(row["accepted_event_id"]),
            host_state=int(row["host_state"]),
            guest_state=int(row["guest_state"]),
            prompt_message_id=int(row["prompt_message_id"]),
            opened_at=_parse_dt(row["opened_at"]),  # type: ignore[arg-type]
            closes_at=_parse_dt(row["closes_at"]),  # type: ignore[arg-type]
            closed_at=_parse_dt(row["closed_at"]),
        )

    def get_feedback_window(self, accepted_event_id: int) -> FeedbackWindowRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_feedback_windows WHERE accepted_event_id = ?",
                (int(accepted_event_id),),
            ).fetchone()
        return self._window(row) if row is not None else None

    def get_feedback_window_for_prompt(self, guest_state: int, prompt_message_id: int) -> FeedbackWindowRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM world_feedback_windows
                WHERE guest_state = ? AND prompt_message_id = ?
                ORDER BY opened_at DESC LIMIT 1
                """,
                (int(guest_state), int(prompt_message_id)),
            ).fetchone()
        return self._window(row) if row is not None else None

    def list_feedback_windows(self) -> list[FeedbackWindowRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_feedback_windows ORDER BY opened_at DESC"
            ).fetchall()
        return [self._window(row) for row in rows]

    def list_due_feedback_windows(self, now: datetime) -> list[FeedbackWindowRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM world_feedback_windows
                WHERE closed_at IS NULL AND closes_at <= ?
                ORDER BY closes_at
                """,
                (_iso(now),),
            ).fetchall()
        return [self._window(row) for row in rows]

    def list_feedback_events(self, guest_state: int, host_state: int, accepted_event_id: int) -> list[WorldEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM world_events
                WHERE event_type = 'state_visit_feedback'
                  AND actor_state = ? AND target_state = ?
                  AND json_extract(payload_json, '$.accepted_event_id') = ?
                ORDER BY created_at ASC, id ASC
                """,
                (int(guest_state), int(host_state), int(accepted_event_id)),
            ).fetchall()
        return [self._event(row) for row in rows]

    def list_visit_showcases(
        self,
        host_state: int,
        guest_state: int,
        *,
        start: datetime,
        end: datetime | None = None,
    ) -> list[WorldEvent]:
        clauses = [
            "event_type = 'state_visit_showcase'",
            "actor_state = ?",
            "target_state = ?",
            "created_at >= ?",
        ]
        params: list[object] = [int(host_state), int(guest_state), _iso(start)]
        if end is not None:
            clauses.append("created_at <= ?")
            params.append(_iso(end))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM world_events WHERE {' AND '.join(clauses)} ORDER BY created_at ASC, id ASC",
                tuple(params),
            ).fetchall()
        return [self._event(row) for row in rows]
