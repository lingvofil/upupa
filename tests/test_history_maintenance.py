"""Automatic history-retention and compaction scheduling tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from core.time_utils import APP_TIMEZONE
from infrastructure.persistence.managed_history import ManagedHistoryRepository
from infrastructure.persistence.sqlite_history import SQLiteHistoryRepository
from services.history_maintenance import (
    HISTORY_RETENTION_DAYS,
    next_maintenance_at,
    run_history_maintenance_once,
    should_compact,
)


def _app_datetime(year, month, day, hour=0, minute=0):
    return APP_TIMEZONE.localize(datetime(year, month, day, hour, minute))


def _record(text: str, timestamp: str):
    return {
        "timestamp": timestamp,
        "chat_id": "-1",
        "chat_title": "Чат",
        "user_id": "2",
        "username": "user2",
        "full_name": "User 2",
        "text": text,
    }


def test_next_maintenance_uses_application_time_boundary():
    before = _app_datetime(2026, 9, 6, 4, 14)
    assert next_maintenance_at(before) == _app_datetime(2026, 9, 6, 4, 15)

    after = _app_datetime(2026, 9, 6, 4, 16)
    assert next_maintenance_at(after) == _app_datetime(2026, 9, 7, 4, 15)


def test_compaction_is_limited_to_first_sunday():
    assert should_compact(_app_datetime(2026, 9, 6, 4, 15)) is True
    assert should_compact(_app_datetime(2026, 9, 13, 4, 15)) is False
    assert should_compact(_app_datetime(2026, 9, 7, 4, 15)) is False


class FakeRepository:
    def __init__(self, log_path: Path, *, deleted=5, mutate_journal=False):
        self.log_path = log_path
        self.deleted = deleted
        self.mutate_journal = mutate_journal
        self.prune_calls = []
        self.compact_calls = 0

    def prune_older_than(self, days, *, now):
        self.prune_calls.append((days, now))
        return self.deleted

    def compact(self):
        self.compact_calls += 1
        if self.mutate_journal:
            self.log_path.write_text("changed", encoding="utf-8")


def test_maintenance_prunes_180_days_and_compacts_monthly(tmp_path):
    journal = tmp_path / "user_messages.log"
    journal.write_text("immutable journal", encoding="utf-8")
    repository = FakeRepository(journal)
    now = _app_datetime(2026, 9, 6, 4, 15)

    result = run_history_maintenance_once(repository, now=now)

    assert repository.prune_calls == [(HISTORY_RETENTION_DAYS, now)]
    assert repository.compact_calls == 1
    assert result["deleted"] == 5
    assert result["compacted"] is True
    assert journal.read_text(encoding="utf-8") == "immutable journal"


def test_maintenance_refuses_journal_mutation(tmp_path):
    journal = tmp_path / "user_messages.log"
    journal.write_text("immutable journal", encoding="utf-8")
    repository = FakeRepository(journal, mutate_journal=True)

    with pytest.raises(RuntimeError, match="user_messages.log"):
        run_history_maintenance_once(
            repository,
            now=_app_datetime(2026, 9, 6, 4, 15),
        )


def test_managed_history_installs_incremental_fts_delete_trigger(tmp_path):
    base = SQLiteHistoryRepository(tmp_path / "history.db", tmp_path / "user_messages.log")
    base.initialize()
    repository = ManagedHistoryRepository(base, tmp_path / "history_deletions.jsonl")
    repository.append(_record("old secret", "2026-01-01T10:00:00"), 1)
    repository.append(_record("keep this", "2026-09-06T10:00:00"), 2)

    with base._connect() as conn:
        trigger = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='history_delete'"
        ).fetchone()
    assert trigger is not None
    assert "VALUES('delete', old.id, old.text)" in trigger[0]

    repository.delete_history(before="2026-02-01T00:00:00")
    assert repository.search(-1, "secret") == []
    assert [row["text"] for row in repository.select(-1)] == ["keep this"]
