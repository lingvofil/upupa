"""Persistent state for Chronicle retrospective indexing."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from infrastructure.persistence.chronicle_schema import (
    BACKFILL_STRATEGY_VERSION,
    init_chronicle_schema,
)
from infrastructure.persistence.sqlite_chronicle_candidates import loads, ts


class SQLiteChronicleBackfillStore:
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
    def _clear_working_set(conn: sqlite3.Connection, chat_id: int) -> None:
        conn.execute("DELETE FROM chronicle_backfill_cluster_phrases WHERE chat_id=?", (chat_id,))
        conn.execute("DELETE FROM chronicle_backfill_clusters WHERE chat_id=?", (chat_id,))
        conn.execute("DELETE FROM chronicle_backfill_phrases WHERE chat_id=?", (chat_id,))

    def request(self, chat_id: int) -> None:
        """Request indexing, upgrading an unfinished v1 scan to strategy v2.

        Saved Chronicle events are preserved. Only transient v1 backfill
        candidates and working data are reset, so the new implementation can
        scan the complete managed history before spending any AI budget.
        """
        chat_id = int(chat_id)
        now = ts()
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM chronicle_backfill_state WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO chronicle_backfill_state(
                        chat_id,status,through_history_id,candidates_examined,ai_requests,
                        requested_at,updated_at,completed_at,scanned_messages,total_messages,
                        queued_candidates,strategy_version
                    ) VALUES (?,'pending',0,0,0,?,?,NULL,0,0,0,?)""",
                    (chat_id, now, now, BACKFILL_STRATEGY_VERSION),
                )
                return

            version = int(row["strategy_version"] or 1)
            if version < BACKFILL_STRATEGY_VERSION:
                self._clear_working_set(conn, chat_id)
                conn.execute(
                    """DELETE FROM chronicle_candidates
                       WHERE chat_id=? AND source='backfill' AND status!='saved'""",
                    (chat_id,),
                )
                conn.execute(
                    """UPDATE chronicle_backfill_state SET
                       status='pending',through_history_id=0,candidates_examined=0,
                       ai_requests=0,requested_at=?,updated_at=?,completed_at=NULL,
                       scanned_messages=0,total_messages=0,queued_candidates=0,
                       strategy_version=? WHERE chat_id=?""",
                    (now, now, BACKFILL_STRATEGY_VERSION, chat_id),
                )

    def state(self, chat_id: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM chronicle_backfill_state WHERE chat_id=?",
                (int(chat_id),),
            ).fetchone()
        return dict(row) if row else None

    def next(self, _max_ai_requests: int | None = None) -> dict | None:
        """Return one chat that still has retrospective work to do.

        The old implementation filtered here by ai_requests, which could stop
        the history scan halfway through. Strategy v2 deliberately does not.
        """
        with closing(self._connect()) as conn:
            row = conn.execute(
                """SELECT * FROM chronicle_backfill_state
                   WHERE status IN ('pending','running','scanning','ranking','classifying')
                     AND strategy_version=?
                   ORDER BY requested_at ASC LIMIT 1""",
                (BACKFILL_STRATEGY_VERSION,),
            ).fetchone()
        return dict(row) if row else None

    def set_total_messages(self, chat_id: int, total_messages: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """UPDATE chronicle_backfill_state
                   SET total_messages=MAX(total_messages,?),updated_at=?
                   WHERE chat_id=?""",
                (max(0, int(total_messages)), ts(), int(chat_id)),
            )

    def record_scan_batch(
        self,
        chat_id: int,
        *,
        through_history_id: int,
        scanned_messages: int,
        phrases: dict[str, tuple[int, datetime, list[int]]],
        clusters: list[dict],
    ) -> bool:
        """Persist one local-scan batch atomically.

        The cursor advances in the same transaction as phrase and cluster
        writes. Retrying after a crash therefore cannot double-count phrases.
        """
        chat_id = int(chat_id)
        through_history_id = int(through_history_id)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            state = conn.execute(
                "SELECT through_history_id FROM chronicle_backfill_state WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
            if state is None or int(state["through_history_id"]) >= through_history_id:
                return False

            for phrase, (count, seen_at, message_ids) in phrases.items():
                if not phrase or count <= 0:
                    continue
                row = conn.execute(
                    """SELECT occurrences,sample_message_ids_json
                       FROM chronicle_backfill_phrases WHERE chat_id=? AND phrase=?""",
                    (chat_id, phrase),
                ).fetchone()
                samples = set(loads(row["sample_message_ids_json"], [])) if row else set()
                samples.update(int(mid) for mid in message_ids if mid is not None)
                new_count = (int(row["occurrences"]) if row else 0) + int(count)
                if row:
                    conn.execute(
                        """UPDATE chronicle_backfill_phrases
                           SET occurrences=?,last_seen=?,sample_message_ids_json=?
                           WHERE chat_id=? AND phrase=?""",
                        (
                            new_count,
                            ts(seen_at),
                            json.dumps(sorted(samples)[-12:]),
                            chat_id,
                            phrase,
                        ),
                    )
                else:
                    conn.execute(
                        """INSERT INTO chronicle_backfill_phrases
                           (chat_id,phrase,occurrences,first_seen,last_seen,sample_message_ids_json)
                           VALUES (?,?,?,?,?,?)""",
                        (
                            chat_id,
                            phrase,
                            new_count,
                            ts(seen_at),
                            ts(seen_at),
                            json.dumps(sorted(samples)[-12:]),
                        ),
                    )

            for cluster in clusters:
                key = str(cluster["cluster_key"])
                conn.execute(
                    """INSERT OR IGNORE INTO chronicle_backfill_clusters(
                       chat_id,cluster_key,history_first_id,history_last_id,started_at,ended_at,
                       message_count,participants_json,source_message_ids_json,anchor_message_id,
                       anchor_text,anchor_user_id,anchor_display_name,anchor_username,
                       reaction_count,base_score,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        chat_id,
                        key,
                        int(cluster["history_first_id"]),
                        int(cluster["history_last_id"]),
                        ts(cluster["started_at"]),
                        ts(cluster["ended_at"]),
                        int(cluster["message_count"]),
                        json.dumps(cluster.get("participants", []), ensure_ascii=False),
                        json.dumps(cluster.get("source_message_ids", [])),
                        cluster.get("anchor_message_id"),
                        str(cluster.get("anchor_text") or "")[:1800],
                        cluster.get("anchor_user_id"),
                        str(cluster.get("anchor_display_name") or "")[:200],
                        cluster.get("anchor_username"),
                        int(cluster.get("reaction_count") or 0),
                        float(cluster.get("base_score") or 0.0),
                        ts(),
                    ),
                )
                for phrase in cluster.get("phrases", []):
                    if phrase:
                        conn.execute(
                            """INSERT OR IGNORE INTO chronicle_backfill_cluster_phrases
                               (chat_id,cluster_key,phrase) VALUES (?,?,?)""",
                            (chat_id, key, str(phrase)),
                        )

            conn.execute(
                """UPDATE chronicle_backfill_state SET
                   status='scanning',through_history_id=?,
                   scanned_messages=scanned_messages+?,
                   candidates_examined=candidates_examined+?,updated_at=?
                   WHERE chat_id=?""",
                (
                    through_history_id,
                    max(0, int(scanned_messages)),
                    len(clusters),
                    ts(),
                    chat_id,
                ),
            )
        return True

    def mark_ranking(self, chat_id: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """UPDATE chronicle_backfill_state
                   SET status='ranking',updated_at=? WHERE chat_id=?""",
                (ts(), int(chat_id)),
            )

    def top_clusters(self, chat_id: int, *, limit: int, min_score: float) -> list[dict]:
        if limit <= 0:
            return []
        chat_id = int(chat_id)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """WITH phrase_strength AS (
                       SELECT cp.cluster_key,MAX(p.occurrences) AS phrase_total
                       FROM chronicle_backfill_cluster_phrases cp
                       JOIN chronicle_backfill_phrases p
                         ON p.chat_id=cp.chat_id AND p.phrase=cp.phrase
                       WHERE cp.chat_id=?
                       GROUP BY cp.cluster_key
                   ), ranked AS (
                       SELECT c.*,COALESCE(ps.phrase_total,0) AS phrase_total,
                              c.base_score + CASE
                                  WHEN COALESCE(ps.phrase_total,0)>=5 THEN 3.0
                                  WHEN COALESCE(ps.phrase_total,0)>=3 THEN 2.0
                                  WHEN COALESCE(ps.phrase_total,0)>=2 THEN 0.8
                                  ELSE 0.0 END AS final_score
                       FROM chronicle_backfill_clusters c
                       LEFT JOIN phrase_strength ps ON ps.cluster_key=c.cluster_key
                       WHERE c.chat_id=?
                   )
                   SELECT * FROM ranked WHERE final_score>=?
                   ORDER BY final_score DESC,message_count DESC,history_first_id ASC
                   LIMIT ?""",
                (chat_id, chat_id, float(min_score), max(1, int(limit))),
            ).fetchall()
            result: list[dict] = []
            for row in rows:
                item = dict(row)
                phrase_rows = conn.execute(
                    """SELECT cp.phrase,p.occurrences
                       FROM chronicle_backfill_cluster_phrases cp
                       JOIN chronicle_backfill_phrases p
                         ON p.chat_id=cp.chat_id AND p.phrase=cp.phrase
                       WHERE cp.chat_id=? AND cp.cluster_key=? AND p.occurrences>=2
                       ORDER BY p.occurrences DESC,length(cp.phrase) DESC LIMIT 5""",
                    (chat_id, item["cluster_key"]),
                ).fetchall()
                item["participants"] = loads(item.pop("participants_json"), [])
                item["source_message_ids"] = [
                    int(mid) for mid in loads(item.pop("source_message_ids_json"), [])
                ]
                item["recurring_phrases"] = [phrase["phrase"] for phrase in phrase_rows]
                result.append(item)
        return result

    def set_classifying(self, chat_id: int, queued_candidates: int) -> None:
        now = ts()
        with closing(self._connect()) as conn, conn:
            if queued_candidates <= 0:
                conn.execute(
                    """UPDATE chronicle_backfill_state SET
                       status='completed',queued_candidates=0,completed_at=?,updated_at=?
                       WHERE chat_id=?""",
                    (now, now, int(chat_id)),
                )
                self._clear_working_set(conn, int(chat_id))
            else:
                conn.execute(
                    """UPDATE chronicle_backfill_state SET
                       status='classifying',queued_candidates=?,updated_at=?
                       WHERE chat_id=?""",
                    (int(queued_candidates), now, int(chat_id)),
                )

    def complete_if_finished(self, chat_id: int) -> bool:
        chat_id = int(chat_id)
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT status FROM chronicle_backfill_state WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
            if row is None or row["status"] != "classifying":
                return False
            pending = int(
                conn.execute(
                    """SELECT COUNT(*) FROM chronicle_candidates
                       WHERE chat_id=? AND source='backfill' AND status='observing'""",
                    (chat_id,),
                ).fetchone()[0]
            )
            if pending:
                return False
            now = ts()
            conn.execute(
                """UPDATE chronicle_backfill_state
                   SET status='completed',completed_at=?,updated_at=? WHERE chat_id=?""",
                (now, now, chat_id),
            )
            self._clear_working_set(conn, chat_id)
            return True

    def update(
        self,
        chat_id: int,
        *,
        through_history_id: int | None = None,
        candidates_delta: int = 0,
        ai_requests_delta: int = 0,
        status: str | None = None,
        completed: bool = False,
    ) -> None:
        """Compatibility update used by Chronicle finalization metrics."""
        sets = ["updated_at=?", "candidates_examined=candidates_examined+?", "ai_requests=ai_requests+?"]
        args: list = [ts(), max(0, int(candidates_delta)), max(0, int(ai_requests_delta))]
        if through_history_id is not None:
            sets.append("through_history_id=MAX(through_history_id,?)")
            args.append(int(through_history_id))
        if status:
            sets.append("status=?")
            args.append(status)
        if completed:
            sets.extend(["status='completed'", "completed_at=?"])
            args.append(ts())
        args.append(int(chat_id))
        with closing(self._connect()) as conn, conn:
            conn.execute(f"UPDATE chronicle_backfill_state SET {','.join(sets)} WHERE chat_id=?", args)

    def record_phrases(
        self,
        chat_id: int,
        phrases: dict[str, tuple[int, datetime, list[int]]],
    ) -> dict[str, int]:
        """Legacy/test helper; production scanning uses record_scan_batch."""
        totals: dict[str, int] = {}
        with closing(self._connect()) as conn, conn:
            for phrase, (count, seen_at, message_ids) in phrases.items():
                if not phrase or count <= 0:
                    continue
                row = conn.execute(
                    """SELECT occurrences,sample_message_ids_json FROM chronicle_backfill_phrases
                       WHERE chat_id=? AND phrase=?""",
                    (int(chat_id), phrase),
                ).fetchone()
                samples = set(loads(row["sample_message_ids_json"], [])) if row else set()
                samples.update(int(mid) for mid in message_ids if mid is not None)
                new_count = (int(row["occurrences"]) if row else 0) + int(count)
                if row:
                    conn.execute(
                        """UPDATE chronicle_backfill_phrases
                           SET occurrences=?,last_seen=?,sample_message_ids_json=?
                           WHERE chat_id=? AND phrase=?""",
                        (new_count, ts(seen_at), json.dumps(sorted(samples)[-12:]), int(chat_id), phrase),
                    )
                else:
                    conn.execute(
                        """INSERT INTO chronicle_backfill_phrases
                           (chat_id,phrase,occurrences,first_seen,last_seen,sample_message_ids_json)
                           VALUES (?,?,?,?,?,?)""",
                        (int(chat_id), phrase, new_count, ts(seen_at), ts(seen_at), json.dumps(sorted(samples)[-12:])),
                    )
                totals[phrase] = new_count
        return totals

    def phrase_counts(self, chat_id: int, phrases: Iterable[str]) -> dict[str, int]:
        clean = sorted({phrase for phrase in phrases if phrase})
        if not clean:
            return {}
        placeholders = ",".join("?" for _ in clean)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT phrase,occurrences FROM chronicle_backfill_phrases WHERE chat_id=? AND phrase IN ({placeholders})",
                [int(chat_id), *clean],
            ).fetchall()
        return {row["phrase"]: int(row["occurrences"]) for row in rows}
