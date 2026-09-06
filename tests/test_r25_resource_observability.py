import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_backup_module():
    path = ROOT / "scripts" / "backup_runtime_state.py"
    spec = importlib.util.spec_from_file_location("r25_backup_runtime_state", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _journal_metadata(backup_dir):
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    return next(item for item in manifest["files"] if item["name"] == "user_messages.log")


def test_journal_backup_reports_created_reused_logical_and_physical_bytes(
    monkeypatch,
    tmp_path,
):
    backup = _load_backup_module()
    monkeypatch.setattr(backup, "_allocated_bytes", lambda path: path.stat().st_size)

    source = tmp_path / "app"
    destination = tmp_path / "backups"
    source.mkdir()
    journal = source / "user_messages.log"
    journal.write_bytes(b"abcdefghTAIL")

    first = backup.create_backup(
        source,
        destination,
        "first",
        journal_chunk_size=8,
    )
    journal.write_bytes(b"abcdefghTAILMORE")
    second = backup.create_backup(
        source,
        destination,
        "second",
        journal_chunk_size=8,
    )

    first_meta = _journal_metadata(first)
    second_meta = _journal_metadata(second)

    assert first_meta["chunk_count"] == 2
    assert first_meta["chunks_created"] == 2
    assert first_meta["chunks_reused"] == 0
    assert first_meta["logical_bytes"] == 12
    assert first_meta["physical_bytes_added"] == 12
    assert first_meta["physical_bytes_method"] == "st_blocks_created_chunks_only"

    assert second_meta["chunk_count"] == 2
    assert second_meta["chunks_created"] == 1
    assert second_meta["chunks_reused"] == 1
    assert second_meta["logical_bytes"] == 16
    assert second_meta["physical_bytes_added"] == 8

    stats = backup._journal_stats(second)
    assert stats == {
        "size": 16,
        "chunk_count": 2,
        "chunks_created": 1,
        "chunks_reused": 1,
        "logical_bytes": 16,
        "physical_bytes_added": 8,
    }
