"""Cross-platform regressions found by the 2026-09-13 audit."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests import test_smoke_imports  # noqa: F401  (fake env + heavy-library mocks)

from features.song import hf_yue2 as yue2
from features.social_graph import rendering
import scripts.backup_runtime_state as backup


def _tracked_mkstemp(tmp_path: Path, holder: dict):
    def fake_mkstemp(*, prefix: str, suffix: str):
        path = tmp_path / f"{prefix}result{suffix}"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        holder.update(fd=fd, path=path)
        return fd, str(path)

    return fake_mkstemp


def test_yue2_closes_mkstemp_fd_before_reusing_path(tmp_path, monkeypatch):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"mp3")
    holder: dict = {}
    monkeypatch.setattr(yue2.tempfile, "mkstemp", _tracked_mkstemp(tmp_path, holder))

    real_unlink = Path.unlink

    def windows_like_unlink(path: Path, *, missing_ok: bool = False):
        if path == holder.get("path"):
            with pytest.raises(OSError):
                os.fstat(holder["fd"])
        return real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", windows_like_unlink)

    output = yue2._download_or_copy_mp3((str(source),))
    try:
        assert output.read_bytes() == b"mp3"
    finally:
        real_unlink(output, missing_ok=True)


def test_yue2_failed_copy_closes_fd_and_removes_temp_file(tmp_path, monkeypatch):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"mp3")
    holder: dict = {}
    monkeypatch.setattr(yue2.tempfile, "mkstemp", _tracked_mkstemp(tmp_path, holder))

    def fail_copy(*_args, **_kwargs):
        raise PermissionError("copy failed")

    monkeypatch.setattr(yue2.shutil, "copyfile", fail_copy)

    with pytest.raises(PermissionError, match="copy failed"):
        yue2._download_or_copy_mp3((str(source),))

    with pytest.raises(OSError):
        os.fstat(holder["fd"])
    assert not holder["path"].exists()


def test_backup_sqlite_connections_are_closed_explicitly(monkeypatch):
    events = []

    class FakeConnection:
        def __init__(self, name: str):
            self.name = name
            self.closed = False

        def backup(self, target):
            events.append(("backup", self.name, target.name))

        def close(self):
            self.closed = True
            events.append(("close", self.name))

    source_conn = FakeConnection("source")
    target_conn = FakeConnection("target")
    connections = iter((source_conn, target_conn))
    monkeypatch.setattr(backup.sqlite3, "connect", lambda *_args, **_kwargs: next(connections))

    backup._backup_sqlite(Path("source.db"), Path("target.db"))

    assert source_conn.closed is True
    assert target_conn.closed is True
    assert events == [
        ("backup", "source", "target"),
        ("close", "target"),
        ("close", "source"),
    ]


def test_backup_closes_source_connection_when_target_open_fails(monkeypatch):
    class FakeConnection:
        closed = False

        def close(self):
            self.closed = True

    source_conn = FakeConnection()
    calls = 0

    def connect(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return source_conn
        raise backup.sqlite3.OperationalError("target locked")

    monkeypatch.setattr(backup.sqlite3, "connect", connect)

    with pytest.raises(backup.sqlite3.OperationalError, match="target locked"):
        backup._backup_sqlite(Path("source.db"), Path("target.db"))

    assert source_conn.closed is True


def test_backup_prune_removes_readonly_chunk_tree(tmp_path):
    destination = tmp_path / "backups"
    incomplete = destination / "20260913T000000.000000Z-broken"
    parts = incomplete / backup.JOURNAL_PARTS_DIR
    parts.mkdir(parents=True)
    chunk = parts / "00000000.chunk"
    chunk.write_bytes(b"immutable")
    chunk.chmod(0o444)

    removed = backup.prune_backups(destination, keep_completed=1)

    assert removed == [incomplete]
    assert not incomplete.exists()


def test_social_graph_uses_bundled_cyrillic_font():
    assert rendering.SOCIAL_GRAPH_FONT_PATH.is_file()
    assert rendering.SOCIAL_GRAPH_FONT_PATH.name == "BerlinSansFBCyrillic-Regular.ttf"

    font = rendering._load_font(20)
    for label in (
        "Детектор SONNE М&M 123",
        "срач доёб пинги реакты винегрет связь",
    ):
        assert rendering._sanitize_label_for_font(label, font) == label


def test_crocodile_party_runtime_path_is_project_canonical():
    from core.paths import PROJECT_ROOT
    from games import crocodile_party_state as party_state

    assert party_state.PARTY_STATE_PATH == PROJECT_ROOT / "crocodile_party_state.json"
