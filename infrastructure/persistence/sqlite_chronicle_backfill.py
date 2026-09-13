"""Backfill cursor and recurring-phrase persistence for Chronicle."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from infrastructure.persistence.chronicle_schema import init_chronicle_schema
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

    def request(self, chat_id: int) -> None:
        now = ts()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT OR IGNORE INTO chronicle_backfill_state
                   (chat_id,status,through_history_id,candidates_examined,ai_requests,requested_at,updated_at)
                   VALUES (?,'pending',0,0,0,?,?)""",
                (int(chat_id), now, now),
            )

    def next(self, max_ai_requests: int) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """SELECT * FROM chronicle_backfill_state
                   WHERE status IN ('pending','running') AND ai_requests<?
                   ORDER BY requested_at ASC LIMIT 1""",
                (max(0, int(max_ai_requests)),),
            ).fetchone()
        return dict(row) if row else None

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
