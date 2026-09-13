"""Durable Chronicle event persistence."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from features.chronicle.models import ChronicleEvent
from infrastructure.persistence.chronicle_schema import init_chronicle_schema
from infrastructure.persistence.sqlite_chronicle_candidates import dt, loads, ts


class SQLiteChronicleEventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def init_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            init_chronicle_schema(conn)

    @staticmethod
    def _people(items: list[dict]) -> list[dict]:
        merged: dict[int, dict] = {}
        for item in items:
            try:
                user_id = int(item["id"])
            except (KeyError, TypeError, ValueError):
                continue
            old = merged.get(user_id, {})
            merged[user_id] = {
                "id": user_id,
                "name": item.get("name") or old.get("name") or "Участник",
                "username": item.get("username") or old.get("username"),
            }
        return list(merged.values())

    def save(
        self,
        *,
        chat_id: int,
        started_at: datetime,
        ended_at: datetime,
        title: str,
        summary: str,
        category: str,
        importance_score: float,
        anchor_message_id: int | None,
        reaction_count: int,
        unique_reactors: int,
        reply_count: int,
        keywords: list[str],
        entities: list[str],
        related_event_ids: list[str],
        ai_metadata: dict,
        source: str,
        participants: list[dict],
        source_message_ids: list[int],
    ) -> str:
        event_id = uuid.uuid4().hex
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO chronicle_events(
                    id,chat_id,created_at,event_started_at,event_ended_at,title,summary,category,
                    importance_score,anchor_message_id,reaction_count,unique_reactors,reply_count,
                    keywords_json,entities_json,related_event_ids_json,ai_metadata_json,source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id, int(chat_id), ts(), ts(started_at), ts(ended_at), title, summary, category,
                    float(importance_score), anchor_message_id, int(reaction_count), int(unique_reactors),
                    int(reply_count), json.dumps(keywords, ensure_ascii=False),
                    json.dumps(entities, ensure_ascii=False), json.dumps(related_event_ids),
                    json.dumps(ai_metadata, ensure_ascii=False), source,
                ),
            )
            for person in self._people(participants):
                conn.execute(
                    """INSERT OR IGNORE INTO chronicle_event_participants
                       (event_id,user_id,display_name,username) VALUES (?,?,?,?)""",
                    (event_id, person["id"], person["name"], person.get("username")),
                )
            for message_id in sorted({int(mid) for mid in source_message_ids if mid is not None}):
                conn.execute(
                    "INSERT OR IGNORE INTO chronicle_event_sources(event_id,message_id) VALUES (?,?)",
                    (event_id, message_id),
                )
            conn.execute(
                """INSERT INTO chronicle_metrics(name,value) VALUES ('chronicle_events_saved_total',1)
                   ON CONFLICT(name) DO UPDATE SET value=value+1"""
            )
        return event_id

    @staticmethod
    def _event(conn: sqlite3.Connection, row: sqlite3.Row) -> ChronicleEvent:
        people = conn.execute(
            """SELECT user_id,display_name,username FROM chronicle_event_participants
               WHERE event_id=? ORDER BY display_name COLLATE NOCASE""",
            (row["id"],),
        ).fetchall()
        sources = conn.execute(
            "SELECT message_id FROM chronicle_event_sources WHERE event_id=? ORDER BY message_id",
            (row["id"],),
        ).fetchall()
        return ChronicleEvent(
            id=row["id"], chat_id=int(row["chat_id"]), created_at=dt(row["created_at"]),
            event_started_at=dt(row["event_started_at"]), event_ended_at=dt(row["event_ended_at"]),
            title=row["title"], summary=row["summary"], category=row["category"],
            importance_score=float(row["importance_score"]),
            participant_ids=[int(item["user_id"]) for item in people],
            participant_names=[item["display_name"] for item in people],
            participant_usernames=[item["username"] for item in people],
            source_message_ids=[int(item["message_id"]) for item in sources],
            anchor_message_id=row["anchor_message_id"], reaction_count=int(row["reaction_count"]),
            unique_reactors=int(row["unique_reactors"]), reply_count=int(row["reply_count"]),
            keywords=loads(row["keywords_json"], []), entities=loads(row["entities_json"], []),
            related_event_ids=loads(row["related_event_ids_json"], []),
            ai_metadata=loads(row["ai_metadata_json"], {}), source=row["source"],
        )

    def list(
        self,
        chat_id: int,
        *,
        limit: int = 8,
        user_id: int | None = None,
        username: str | None = None,
    ) -> list[ChronicleEvent]:
        clauses = ["e.chat_id=?"]
        args: list = [int(chat_id)]
        join = ""
        if user_id is not None or username is not None:
            join = " JOIN chronicle_event_participants p ON p.event_id=e.id "
            if user_id is not None:
                clauses.append("p.user_id=?")
                args.append(int(user_id))
            else:
                clauses.append("p.username=? COLLATE NOCASE")
                args.append((username or "").lstrip("@"))
        args.append(max(1, int(limit)))
        sql = (
            "SELECT DISTINCT e.* FROM chronicle_events e" + join
            + " WHERE " + " AND ".join(clauses)
            + " ORDER BY e.event_started_at DESC,e.importance_score DESC LIMIT ?"
        )
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, args).fetchall()
            return [self._event(conn, row) for row in rows]

    def recent(self, chat_id: int, *, days: int = 30, limit: int = 50) -> list[ChronicleEvent]:
        cutoff = ts(datetime.now(timezone.utc) - timedelta(days=max(1, days)))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT * FROM chronicle_events WHERE chat_id=? AND event_ended_at>=?
                   ORDER BY event_started_at DESC LIMIT ?""",
                (int(chat_id), cutoff, max(1, int(limit))),
            ).fetchall()
            return [self._event(conn, row) for row in rows]

    def count_since(self, chat_id: int, since: datetime) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM chronicle_events WHERE chat_id=? AND created_at>=?",
                (int(chat_id), ts(since)),
            ).fetchone()[0])

    def participant_count_since(self, chat_id: int, user_id: int, since: datetime) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute(
                """SELECT COUNT(*) FROM chronicle_events e
                   JOIN chronicle_event_participants p ON p.event_id=e.id
                   WHERE e.chat_id=? AND p.user_id=? AND e.created_at>=?""",
                (int(chat_id), int(user_id), ts(since)),
            ).fetchone()[0])

    def latest_created_at(self, chat_id: int) -> datetime | None:
        with closing(self._connect()) as conn:
            value = conn.execute(
                "SELECT MAX(created_at) FROM chronicle_events WHERE chat_id=?",
                (int(chat_id),),
            ).fetchone()[0]
        return dt(value) if value else None
