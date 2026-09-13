"""Candidate/reaction persistence for Chronicle."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import statistics
from typing import Iterable

from features.chronicle.models import ChronicleCandidate
from infrastructure.persistence.chronicle_schema import init_chronicle_schema


def utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def ts(value: datetime | None = None) -> str:
    return utc(value).isoformat(timespec="seconds")


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return utc(parsed)


def loads(raw: str | None, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


class SQLiteChronicleCandidateStore:
    def __init__(self, path: str | Path, statistics_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.statistics_path = Path(statistics_path) if statistics_path else None

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
    def _participants(existing: list[dict], incoming: Iterable[dict]) -> list[dict]:
        merged: dict[int, dict] = {}
        for item in [*existing, *incoming]:
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

    def metric(self, name: str, amount: int = 1, *, conn: sqlite3.Connection | None = None) -> None:
        owns = conn is None
        conn = conn or self._connect()
        try:
            conn.execute(
                """INSERT INTO chronicle_metrics(name,value) VALUES (?,?)
                   ON CONFLICT(name) DO UPDATE SET value=value+excluded.value""",
                (name, int(amount)),
            )
            if owns:
                conn.commit()
        finally:
            if owns:
                conn.close()

    def upsert_candidate(
        self,
        *,
        chat_id: int,
        candidate_key: str,
        timestamp: datetime,
        due_at: datetime,
        score_delta: float = 0.0,
        score_floor: float | None = None,
        anchor_message_id: int | None = None,
        anchor_text: str = "",
        anchor_user_id: int | None = None,
        anchor_display_name: str = "",
        anchor_username: str | None = None,
        reaction_count: int | None = None,
        unique_reactors: int | None = None,
        reply_delta: int = 0,
        participants: Iterable[dict] = (),
        source_message_ids: Iterable[int] = (),
        source: str = "live",
        metadata: dict | None = None,
    ) -> int:
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT * FROM chronicle_candidates WHERE chat_id=? AND candidate_key=?",
                (int(chat_id), candidate_key),
            ).fetchone()
            incoming_participants = list(participants)
            incoming_sources = {int(mid) for mid in source_message_ids if mid is not None}
            if row is None:
                people = self._participants([], incoming_participants)
                score = max(float(score_delta), float(score_floor or 0.0))
                cursor = conn.execute(
                    """INSERT INTO chronicle_candidates(
                        chat_id,candidate_key,anchor_message_id,started_at,last_activity_at,due_at,
                        anchor_text,anchor_user_id,anchor_display_name,anchor_username,score,
                        reaction_count,unique_reactors,reply_count,participants_json,
                        source_message_ids_json,source,metadata_json,status,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(chat_id), candidate_key, anchor_message_id, ts(timestamp), ts(timestamp), ts(due_at),
                        anchor_text, anchor_user_id, anchor_display_name, anchor_username, score,
                        int(reaction_count or 0), int(unique_reactors or 0), max(0, int(reply_delta)),
                        json.dumps(people, ensure_ascii=False), json.dumps(sorted(incoming_sources)),
                        source, json.dumps(metadata or {}, ensure_ascii=False), "observing", ts(),
                    ),
                )
                self.metric("chronicle_candidates_total", conn=conn)
                return int(cursor.lastrowid)

            people = self._participants(loads(row["participants_json"], []), incoming_participants)
            sources = set(loads(row["source_message_ids_json"], [])) | incoming_sources
            old_meta = loads(row["metadata_json"], {})
            old_meta.update(metadata or {})
            score = float(row["score"]) + float(score_delta)
            if score_floor is not None:
                score = max(score, float(score_floor))
            conn.execute(
                """UPDATE chronicle_candidates SET
                    anchor_message_id=COALESCE(anchor_message_id,?),
                    started_at=CASE WHEN started_at>? THEN ? ELSE started_at END,
                    last_activity_at=?, due_at=?,
                    anchor_text=CASE WHEN anchor_text='' THEN ? ELSE anchor_text END,
                    anchor_user_id=COALESCE(anchor_user_id,?),
                    anchor_display_name=CASE WHEN anchor_display_name='' THEN ? ELSE anchor_display_name END,
                    anchor_username=COALESCE(anchor_username,?),
                    score=?,
                    reaction_count=CASE WHEN ? IS NULL THEN reaction_count ELSE MAX(reaction_count,?) END,
                    unique_reactors=CASE WHEN ? IS NULL THEN unique_reactors ELSE MAX(unique_reactors,?) END,
                    reply_count=reply_count+?,
                    participants_json=?,source_message_ids_json=?,metadata_json=?,
                    status=CASE WHEN status='rejected' THEN status ELSE 'observing' END
                  WHERE id=?""",
                (
                    anchor_message_id, ts(timestamp), ts(timestamp), ts(timestamp), ts(due_at),
                    anchor_text, anchor_user_id, anchor_display_name, anchor_username, score,
                    reaction_count, reaction_count, unique_reactors, unique_reactors,
                    max(0, int(reply_delta)), json.dumps(people, ensure_ascii=False),
                    json.dumps(sorted(sources)), json.dumps(old_meta, ensure_ascii=False), row["id"],
                ),
            )
            return int(row["id"])

    @staticmethod
    def _candidate(row: sqlite3.Row) -> ChronicleCandidate:
        people = loads(row["participants_json"], [])
        metadata = loads(row["metadata_json"], {})
        metadata.setdefault("participants", people)
        return ChronicleCandidate(
            id=int(row["id"]), chat_id=int(row["chat_id"]), candidate_key=row["candidate_key"],
            anchor_message_id=row["anchor_message_id"], started_at=dt(row["started_at"]),
            last_activity_at=dt(row["last_activity_at"]), due_at=dt(row["due_at"]),
            anchor_text=row["anchor_text"], anchor_user_id=row["anchor_user_id"],
            anchor_display_name=row["anchor_display_name"], anchor_username=row["anchor_username"],
            score=float(row["score"]), reaction_count=int(row["reaction_count"]),
            unique_reactors=int(row["unique_reactors"]), reply_count=int(row["reply_count"]),
            participant_count=len(people), participant_ids=[int(p["id"]) for p in people if "id" in p],
            source_message_ids=[int(mid) for mid in loads(row["source_message_ids_json"], [])],
            source=row["source"], metadata=metadata,
        )

    def get_candidate(self, candidate_id: int) -> ChronicleCandidate | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM chronicle_candidates WHERE id=?", (int(candidate_id),)).fetchone()
        return self._candidate(row) if row else None

    def due_candidates(self, now: datetime, limit: int = 10) -> list[ChronicleCandidate]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT * FROM chronicle_candidates
                   WHERE status='observing' AND due_at<=?
                   ORDER BY score DESC,due_at ASC LIMIT ?""",
                (ts(now), max(1, int(limit))),
            ).fetchall()
        return [self._candidate(row) for row in rows]

    def mark_candidate(self, candidate_id: int, status: str, reason: str | None = None) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE chronicle_candidates SET status=?,reject_reason=? WHERE id=?",
                (status, reason, int(candidate_id)),
            )
            if status == "rejected":
                self.metric("chronicle_candidates_rejected_total", conn=conn)

    def expire(self, older_than: datetime) -> int:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """UPDATE chronicle_candidates SET status='rejected',reject_reason='expired'
                   WHERE status='observing' AND last_activity_at<?""",
                (ts(older_than),),
            )
            return max(0, cursor.rowcount)

    def record_user_reaction(
        self, chat_id: int, message_id: int, user_id: int, reactions: list[str], when: datetime
    ) -> tuple[int, int]:
        with closing(self._connect()) as conn, conn:
            if reactions:
                conn.execute(
                    """INSERT INTO chronicle_user_reactions VALUES (?,?,?,?,?)
                       ON CONFLICT(chat_id,message_id,user_id) DO UPDATE SET
                       reactions_json=excluded.reactions_json,updated_at=excluded.updated_at""",
                    (int(chat_id), int(message_id), int(user_id), json.dumps(reactions, ensure_ascii=False), ts(when)),
                )
            else:
                conn.execute(
                    "DELETE FROM chronicle_user_reactions WHERE chat_id=? AND message_id=? AND user_id=?",
                    (int(chat_id), int(message_id), int(user_id)),
                )
        return self.reaction_totals(chat_id, message_id)

    def record_aggregate_reaction(
        self, chat_id: int, message_id: int, total_count: int, type_count: int, when: datetime
    ) -> tuple[int, int]:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO chronicle_reaction_aggregate VALUES (?,?,?,?,?)
                   ON CONFLICT(chat_id,message_id) DO UPDATE SET
                   total_count=excluded.total_count,type_count=excluded.type_count,updated_at=excluded.updated_at""",
                (int(chat_id), int(message_id), max(0, int(total_count)), max(0, int(type_count)), ts(when)),
            )
        return self.reaction_totals(chat_id, message_id)

    def reaction_totals(self, chat_id: int, message_id: int) -> tuple[int, int]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT reactions_json FROM chronicle_user_reactions WHERE chat_id=? AND message_id=?",
                (int(chat_id), int(message_id)),
            ).fetchall()
            aggregate = conn.execute(
                "SELECT total_count FROM chronicle_reaction_aggregate WHERE chat_id=? AND message_id=?",
                (int(chat_id), int(message_id)),
            ).fetchone()
        unique = sum(bool(loads(row["reactions_json"], [])) for row in rows)
        user_total = sum(len(loads(row["reactions_json"], [])) for row in rows)
        return max(user_total, int(aggregate["total_count"]) if aggregate else 0), unique

    def reaction_baseline(self, chat_id: int) -> tuple[float, float]:
        if self.statistics_path is None or not self.statistics_path.exists():
            return 0.0, 0.0
        conn = sqlite3.connect(self.statistics_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            cutoff = ts(datetime.now(timezone.utc) - timedelta(days=90))
            rows = conn.execute(
                """SELECT COUNT(*) n FROM social_interactions
                   WHERE chat_id=? AND interaction_type='reaction' AND interaction_timestamp>=?
                   GROUP BY message_id""",
                (int(chat_id), cutoff),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()
        counts = sorted(int(row["n"]) for row in rows if int(row["n"]) > 0)
        if not counts:
            return 0.0, 0.0
        median = float(statistics.median(counts))
        p90 = float(counts[min(len(counts) - 1, round((len(counts) - 1) * 0.9))])
        return median, p90

    def historical_reaction_counts(self, chat_id: int, message_ids: list[int]) -> dict[int, int]:
        if not message_ids or self.statistics_path is None or not self.statistics_path.exists():
            return {}
        conn = sqlite3.connect(self.statistics_path, timeout=10)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in message_ids)
        try:
            rows = conn.execute(
                f"""SELECT message_id,COUNT(*) n FROM social_interactions
                    WHERE chat_id=? AND interaction_type='reaction' AND message_id IN ({placeholders})
                    GROUP BY message_id""",
                [int(chat_id), *[int(mid) for mid in message_ids]],
            ).fetchall()
            return {int(row["message_id"]): int(row["n"]) for row in rows}
        except sqlite3.OperationalError:
            return {}
        finally:
            conn.close()
