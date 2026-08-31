"""Persistent metadata and event ledger for the World of Upupa.

The core diplomatic repository keeps the current state.  This module adds the
historical/ornamental layer: state identity, ambassadors, alliance names and an
append-only journal used by the hub, map, news and Radio Upupa.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from features.world.models import WorldState


_GOVERNMENT_FORMS = (
    "управляемая анархия",
    "диванная республика",
    "олигархия админов",
    "мемократическая федерация",
    "просвещённый бардак",
    "парламентская тусовка",
    "технократическая хунта",
    "конституционная попойка",
    "народная монархия",
    "советская демократия в одном чате",
)

_CLIMATES = (
    "умеренно токсичный, местами с прояснениями",
    "душный с редкими порывами здравого смысла",
    "тёплый ламповый с локальными грозами",
    "резко континентальный: от любви до срача за три сообщения",
    "влажный мемный",
    "нестабильный, к вечеру возможны голосовые",
    "субтропический бардак",
    "вечная пятница с кратковременным понедельником",
    "пасмурный, но с мемами",
    "повышенной разговорной активности",
)

_MAIN_THREATS = (
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


@dataclass(frozen=True)
class WorldDetails:
    world_id: int
    government_form: str
    climate: str
    main_threat: str
    ambassador_user_id: int | None = None
    ambassador_name: str | None = None


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


class WorldLedger:
    """SQLite-backed history/metadata layer sharing the World database file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

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

    @staticmethod
    def _identity(state: WorldState) -> tuple[str, str, str]:
        digest = hashlib.sha256(f"{state.world_id}:{state.title}".encode("utf-8")).digest()
        return (
            _GOVERNMENT_FORMS[digest[0] % len(_GOVERNMENT_FORMS)],
            _CLIMATES[digest[1] % len(_CLIMATES)],
            _MAIN_THREATS[digest[2] % len(_MAIN_THREATS)],
        )

    @staticmethod
    def _details(row: sqlite3.Row | None) -> WorldDetails | None:
        if row is None:
            return None
        return WorldDetails(
            world_id=int(row["world_id"]),
            government_form=str(row["government_form"]),
            climate=str(row["climate"]),
            main_threat=str(row["main_threat"]),
            ambassador_user_id=(int(row["ambassador_user_id"]) if row["ambassador_user_id"] is not None else None),
            ambassador_name=(str(row["ambassador_name"]) if row["ambassador_name"] else None),
        )

    def ensure_details(self, state: WorldState) -> WorldDetails:
        government_form, climate, main_threat = self._identity(state)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO world_details(
                    world_id, government_form, climate, main_threat, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (state.world_id, government_form, climate, main_threat, self._now()),
            )
            row = conn.execute(
                "SELECT * FROM world_details WHERE world_id = ?",
                (state.world_id,),
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
        when = (created_at or datetime.now(timezone.utc)).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO world_events(
                        event_type, actor_state, target_state, payload_json, dedupe_key, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_type,
                        actor_state,
                        target_state,
                        json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                        dedupe_key,
                        when,
                    ),
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
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return WorldEvent(
            event_id=int(row["id"]),
            event_type=str(row["event_type"]),
            actor_state=(int(row["actor_state"]) if row["actor_state"] is not None else None),
            target_state=(int(row["target_state"]) if row["target_state"] is not None else None),
            payload=payload,
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def list_events(
        self,
        *,
        limit: int = 30,
        days: int | None = None,
        world_id: int | None = None,
        event_types: set[str] | None = None,
    ) -> list[WorldEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if days is not None:
            since = datetime.now(timezone.utc) - timedelta(days=max(0, days))
            clauses.append("created_at >= ?")
            params.append(since.isoformat())
        if world_id is not None:
            clauses.append("(actor_state = ? OR target_state = ?)")
            params.extend((world_id, world_id))
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(sorted(event_types))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM world_events {where} ORDER BY created_at DESC, id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [self._event(row) for row in rows]

    def list_relations(self, *, active_only: bool = True) -> list[WorldRelation]:
        enabled_filter = "AND a.enabled = 1 AND b.enabled = 1" if active_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT dr.state_a, dr.state_b, dr.relation, wam.name AS alliance_name
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
            )
            for row in rows
        ]
