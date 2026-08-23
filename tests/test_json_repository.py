import json

import pytest

from core.json_repository import JsonFileRepository


def test_json_file_repository_round_trip_creates_parent(tmp_path):
    path = tmp_path / "nested" / "state.json"
    repository = JsonFileRepository(path)

    repository.save({"чат": [1, 2, 3]})

    assert repository.load() == {"чат": [1, 2, 3]}
    assert json.loads(path.read_text(encoding="utf-8")) == {"чат": [1, 2, 3]}


def test_json_file_repository_keeps_old_file_if_atomic_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    repository = JsonFileRepository(path)
    repository.save({"version": "old"})

    def fail_replace(source, target):
        raise OSError("replace failed")

    monkeypatch.setattr("core.json_repository.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        repository.save({"version": "new"})

    assert repository.load() == {"version": "old"}
    assert not list(tmp_path.glob(".state.json.*.tmp"))


def test_json_file_repository_cleans_temp_file_if_serialization_fails(tmp_path):
    path = tmp_path / "state.json"
    repository = JsonFileRepository(path)

    with pytest.raises(TypeError):
        repository.save({"not-json": object()})

    assert not path.exists()
    assert not list(tmp_path.glob(".state.json.*.tmp"))


def test_json_file_repository_reports_missing_file(tmp_path):
    repository = JsonFileRepository(tmp_path / "missing.json")

    with pytest.raises(FileNotFoundError):
        repository.load()
