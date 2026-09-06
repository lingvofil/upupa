"""History retention/deletion layer over the immutable recovery journal.

``user_messages.log`` remains append-only. Logical deletions are written to a
separate append-only JSONL ledger and replayed after rebuilding ``history.db``.
This preserves disaster recovery without silently resurrecting deleted rows.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from core.settings import APP_TIMEZONE_NAME
from core.time_utils import app_now, app_now_naive, history_timestamp, parse_history_datetime


DELETION_LEDGER_FILENAME = "history_deletions.jsonl"
DELETION_EVENT_VERSION = 1


class HistoryDeletionLedgerCorrupt(RuntimeError):
    """Deletion ledger cannot be trusted; startup should fail closed."""


class ManagedHistoryRepository:
    """Proxy that adds timezone normalization and durable logical deletion."""

    def __init__(self, repository, deletion_log_path: str | Path | None = None):
        self._repository = repository
        self.path = Path(repository.path)
        self.log_path = Path(repository.log_path).resolve()
        self.deletion_log_path = Path(
            deletion_log_path or self.path.with_name(DELETION_LEDGER_FILENAME)
        ).resolve()
        self._lock = getattr(repository, "_lock", threading.RLock())
        self.apply_deletion_ledger()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    def append(self, record: dict, message_id: int | None = None) -> None:
        """Write app-time timestamps independent of the VPS host timezone."""
        normalized = dict(record)
        normalized["timestamp"] = history_timestamp(
            parse_history_datetime(str(record["timestamp"]))
        )
        self._repository.append(normalized, message_id)

    @staticmethod
    def _ensure_schema(conn) -> None:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS history_deletion_events (
                event_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                deleted_count INTEGER NOT NULL
            )"""
        )

    @staticmethod
    def _validate_event(event: dict) -> dict:
        if not isinstance(event, dict):
            raise HistoryDeletionLedgerCorrupt("history deletion event is not an object")
        if event.get("version") != DELETION_EVENT_VERSION:
            raise HistoryDeletionLedgerCorrupt("unsupported history deletion event version")
        event_id = event.get("event_id")
        created_at = event.get("created_at")
        if not isinstance(event_id, str) or not event_id.strip():
            raise HistoryDeletionLedgerCorrupt("history deletion event has no event_id")
        if not isinstance(created_at, str) or not created_at.strip():
            raise HistoryDeletionLedgerCorrupt("history deletion event has no created_at")

        chat_id = event.get("chat_id")
        user_id = event.get("user_id")
        before = event.get("before")
        if chat_id is None and user_id is None and before is None:
            raise HistoryDeletionLedgerCorrupt("refusing unscoped full-history deletion")
        if before is not None:
            try:
                parse_history_datetime(str(before))
            except (TypeError, ValueError) as exc:
                raise HistoryDeletionLedgerCorrupt("invalid history deletion cutoff") from exc
        return event

    def _read_ledger(self) -> list[dict]:
        if not self.deletion_log_path.exists():
            return []
        events: list[dict] = []
        seen: dict[str, str] = {}
        with self.deletion_log_path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    event = self._validate_event(json.loads(line))
                except (json.JSONDecodeError, HistoryDeletionLedgerCorrupt) as exc:
                    raise HistoryDeletionLedgerCorrupt(
                        f"invalid deletion ledger line {line_number}: {exc}"
                    ) from exc
                canonical = json.dumps(event, ensure_ascii=False, sort_keys=True)
                previous = seen.get(event["event_id"])
                if previous is not None and previous != canonical:
                    raise HistoryDeletionLedgerCorrupt(
                        f"conflicting duplicate deletion event {event['event_id']}"
                    )
                if previous is None:
                    seen[event["event_id"]] = canonical
                    events.append(event)
        return events

    @staticmethod
    def _event_where(event: dict) -> tuple[str, list[str]]:
        clauses = ["1"]
        args: list[str] = []
        if event.get("chat_id") is not None:
            clauses.append("chat_id=?")
            args.append(str(event["chat_id"]))
        if event.get("user_id") is not None:
            clauses.append("user_id=?")
            args.append(str(event["user_id"]))
        if event.get("before") is not None:
            clauses.append("timestamp < ?")
            args.append(history_timestamp(parse_history_datetime(str(event["before"]))))
        return " AND ".join(clauses), args

    def _apply_events(self, events: list[dict]) -> int:
        total_deleted = 0
        with closing(self._repository._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_schema(conn)
            applied = {
                row[0]
                for row in conn.execute("SELECT event_id FROM history_deletion_events")
            }
            rebuild_fts = False
            for event in events:
                event = self._validate_event(event)
                if event["event_id"] in applied:
                    continue
                where, args = self._event_where(event)
                deleted = conn.execute(
                    f"SELECT COUNT(*) FROM history_messages WHERE {where}", args
                ).fetchone()[0]
                if deleted:
                    conn.execute(f"DELETE FROM history_messages WHERE {where}", args)
                    rebuild_fts = True
                conn.execute(
                    "INSERT INTO history_deletion_events VALUES (?, ?, ?, ?)",
                    (
                        event["event_id"],
                        event["created_at"],
                        json.dumps(event, ensure_ascii=False, sort_keys=True),
                        int(deleted),
                    ),
                )
                applied.add(event["event_id"])
                total_deleted += int(deleted)
            if rebuild_fts:
                # history_fts is an external-content table and the legacy schema
                # only has an INSERT trigger. Rebuild after rare admin deletions.
                conn.execute("INSERT INTO history_fts(history_fts) VALUES('rebuild')")
        return total_deleted

    def apply_deletion_ledger(self) -> int:
        """Replay durable deletions after normal journal synchronization."""
        with self._lock:
            self._repository.synchronize()
            events = self._read_ledger()
            with closing(self._repository._connect()) as conn, conn:
                self._ensure_schema(conn)
                applied_rows = {
                    row[0]
                    for row in conn.execute("SELECT event_id FROM history_deletion_events")
                }
            ledger_ids = {event["event_id"] for event in events}
            missing_from_ledger = applied_rows - ledger_ids
            if missing_from_ledger:
                raise HistoryDeletionLedgerCorrupt(
                    "history.db contains deletion events missing from the durable ledger"
                )
            return self._apply_events(events)

    def _append_ledger_event(self, event: dict) -> None:
        self.deletion_log_path.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        fd = os.open(
            self.deletion_log_path,
            os.O_CREAT | os.O_WRONLY | os.O_APPEND,
            0o600,
        )
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("failed to append history deletion ledger")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def delete_history(
        self,
        *,
        chat_id: int | str | None = None,
        user_id: int | str | None = None,
        before: datetime | str | None = None,
    ) -> int:
        """Log and apply a durable logical deletion without truncating the journal."""
        if chat_id is None and user_id is None and before is None:
            raise ValueError("at least one deletion scope is required")
        cutoff = None
        if before is not None:
            cutoff = history_timestamp(parse_history_datetime(before))
        event = {
            "version": DELETION_EVENT_VERSION,
            "event_id": str(uuid4()),
            "created_at": app_now().isoformat(timespec="microseconds"),
            "timezone": APP_TIMEZONE_NAME,
            "chat_id": None if chat_id is None else str(chat_id),
            "user_id": None if user_id is None else str(user_id),
            "before": cutoff,
        }
        with self._lock:
            self._repository.synchronize()
            self._append_ledger_event(event)
            return self._apply_events([event])

    def prune_older_than(
        self,
        days: int,
        *,
        chat_id: int | str | None = None,
        now: datetime | None = None,
    ) -> int:
        if days <= 0:
            raise ValueError("retention days must be positive")
        reference = parse_history_datetime(now) if now is not None else parse_history_datetime(app_now_naive())
        cutoff = reference - timedelta(days=days)
        return self.delete_history(chat_id=chat_id, before=cutoff)

    def compact(self) -> None:
        """Explicitly reclaim SQLite space after an administrative deletion."""
        with self._lock:
            self._repository.synchronize()
            with closing(self._repository._connect()) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("VACUUM")

    def deletion_events(self) -> list[dict]:
        return self._read_ledger()
