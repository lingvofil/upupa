"""Retention/deletion/export regression tests for managed history."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from core.settings import APP_TIMEZONE_NAME
from core.time_utils import history_timestamp
from infrastructure.persistence.managed_history import (
    HistoryDeletionLedgerCorrupt,
    ManagedHistoryRepository,
)
from infrastructure.persistence.sqlite_history import SQLiteHistoryRepository
from scripts.backup_runtime_state import create_backup
from scripts.history_admin import export_history


def _record(text: str, timestamp: str, *, chat=-1, user=2):
    return {
        "timestamp": timestamp,
        "chat_id": str(chat),
        "chat_title": "Чат",
        "user_id": str(user),
        "username": f"user{user}",
        "full_name": f"User {user}",
        "text": text,
    }


def _managed(tmp_path: Path) -> ManagedHistoryRepository:
    base = SQLiteHistoryRepository(tmp_path / "history.db", tmp_path / "user_messages.log")
    base.initialize()
    return ManagedHistoryRepository(base, tmp_path / "history_deletions.jsonl")


def _remove_sqlite(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        candidate.unlink(missing_ok=True)


def test_history_timestamp_uses_explicit_application_timezone():
    assert APP_TIMEZONE_NAME == "Europe/Moscow"
    assert history_timestamp(datetime(2026, 9, 6, 12, tzinfo=timezone.utc)) == (
        "2026-09-06T15:00:00.000000"
    )
    # Legacy naive timestamps are defined as application wall clock, not host TZ.
    assert history_timestamp(datetime(2026, 9, 6, 15)) == "2026-09-06T15:00:00.000000"


def test_durable_retention_never_truncates_journal_and_survives_rebuild(tmp_path):
    repository = _managed(tmp_path)
    repository.append(_record("old secret", "2026-09-01T10:00:00"), 1)
    repository.append(_record("keep this", "2026-09-06T14:00:00"), 2)
    journal_before = repository.log_path.read_bytes()

    deleted = repository.delete_history(before="2026-09-03T00:00:00")

    assert deleted == 1
    assert repository.log_path.read_bytes() == journal_before
    assert [row["text"] for row in repository.select(-1)] == ["keep this"]
    assert repository.search(-1, "secret") == []
    assert repository.deletion_log_path.exists()

    # Simulate total loss of the derived index. The raw journal alone contains
    # both messages, then ManagedHistory replays the durable deletion ledger.
    _remove_sqlite(repository.path)
    rebuilt_base = SQLiteHistoryRepository(repository.path, repository.log_path)
    rebuilt_base.initialize()
    assert rebuilt_base.count(-1) == 2

    rebuilt = ManagedHistoryRepository(rebuilt_base, repository.deletion_log_path)
    assert [row["text"] for row in rebuilt.select(-1)] == ["keep this"]


def test_scoped_user_delete_preserves_other_users_and_chats(tmp_path):
    repository = _managed(tmp_path)
    repository.append(_record("delete me", "2026-09-06T10:00:00", chat=-1, user=2), 1)
    repository.append(_record("other user", "2026-09-06T10:01:00", chat=-1, user=3), 2)
    repository.append(_record("other chat", "2026-09-06T10:02:00", chat=-10, user=2), 3)

    assert repository.delete_history(chat_id=-1, user_id=2) == 1
    assert [row["text"] for row in repository.select(-1)] == ["other user"]
    assert [row["text"] for row in repository.select(-10)] == ["other chat"]


def test_corrupt_deletion_ledger_fails_closed(tmp_path):
    base = SQLiteHistoryRepository(tmp_path / "history.db", tmp_path / "user_messages.log")
    base.initialize()
    base.append(_record("must not silently resurrect", "2026-09-06T10:00:00"), 1)
    ledger = tmp_path / "history_deletions.jsonl"
    ledger.write_text('{"version": 1, "event_id": "broken"', encoding="utf-8")

    with pytest.raises(HistoryDeletionLedgerCorrupt):
        ManagedHistoryRepository(base, ledger)


def test_unscoped_full_delete_is_rejected(tmp_path):
    repository = _managed(tmp_path)
    with pytest.raises(ValueError):
        repository.delete_history()


def test_export_streams_only_logically_retained_rows(tmp_path):
    repository = _managed(tmp_path)
    repository.append(_record("old", "2026-09-01T10:00:00"), 1)
    repository.append(_record("new", "2026-09-06T10:00:00"), 2)
    repository.delete_history(before="2026-09-03T00:00:00")

    output = tmp_path / "export.jsonl"
    count = export_history(repository, output, format_name="jsonl", chat_id="-1")
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert count == 1
    assert rows[0]["text"] == "new"
    assert rows[0]["timezone"] == "Europe/Moscow"


def test_runtime_backup_includes_deletion_ledger(tmp_path):
    source = tmp_path / "app"
    source.mkdir()
    (source / "history_deletions.jsonl").write_text('{"event_id":"x"}\n', encoding="utf-8")
    (source / "user_messages.log").write_text("journal\n", encoding="utf-8")
    destination = tmp_path / "backups"

    backup = create_backup(source, destination, "history-test")
    assert (backup / "history_deletions.jsonl").read_text(encoding="utf-8") == '{"event_id":"x"}\n'
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert "history_deletions.jsonl" in {item["name"] for item in manifest["files"]}
