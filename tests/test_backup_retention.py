"""Deploy backup rotation and disk-pressure regression tests."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

import scripts.backup_runtime_state as backup


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "app"
    source.mkdir()
    with sqlite3.connect(source / "history.db") as conn:
        conn.execute("CREATE TABLE messages(text TEXT)")
        conn.execute("INSERT INTO messages VALUES ('preserved')")
    (source / "user_messages.log").write_text("journal\n", encoding="utf-8")
    return source


def test_create_backup_keeps_only_three_latest_completed_snapshots(tmp_path):
    source = _source(tmp_path)
    destination = tmp_path / "backups"
    created = [
        backup.create_backup(source, destination, f"sha-{index}", keep=3)
        for index in range(5)
    ]

    remaining = backup._completed_backups(destination)
    assert len(remaining) == 3
    assert [path.name for path in remaining] == [path.name for path in created[-3:]]
    assert all((path / "manifest.json").is_file() for path in remaining)


def test_prune_backups_removes_incomplete_snapshot(tmp_path):
    destination = tmp_path / "backups"
    destination.mkdir()
    complete = destination / "20260906T000000.000000Z-good"
    complete.mkdir()
    (complete / "manifest.json").write_text(
        json.dumps({"files": []}),
        encoding="utf-8",
    )
    incomplete = destination / "20260906T000001.000000Z-broken"
    incomplete.mkdir()
    (incomplete / "partial.db").write_bytes(b"partial")

    removed = backup.prune_backups(destination, keep_completed=1)

    assert incomplete in removed
    assert not incomplete.exists()
    assert complete.exists()


def test_disk_full_failure_keeps_last_good_backup_and_removes_partial(tmp_path, monkeypatch):
    source = _source(tmp_path)
    destination = tmp_path / "backups"
    good = backup.create_backup(source, destination, "good", keep=3)

    def fail_backup(_source, _target):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(backup, "_backup_sqlite", fail_backup)

    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        backup.create_backup(source, destination, "next", keep=3)

    assert backup._completed_backups(destination) == [good]
    matching = [
        path for path in destination.iterdir()
        if path.is_dir() and backup.BACKUP_DIR_RE.fullmatch(path.name)
    ]
    assert matching == [good]
