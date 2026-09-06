import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from infrastructure.persistence.sqlite_rank_counters import SQLiteRankCountersRepository


TODAY = date(2026, 9, 5)


@pytest.fixture
def repo(tmp_path):
    repository = SQLiteRankCountersRepository(tmp_path / "statistics.db")
    repository.initialize(tmp_path / "missing.json")
    return repository


def test_migration_preserves_counts_and_is_not_replayed(tmp_path):
    legacy = tmp_path / "message_stats.json"
    original = {"-1": {"2": {"total": 123, "daily": 3, "weekly": 20,
        "last_daily_reset": "2026-09-05", "last_weekly_reset": "2026-09-01"}}}
    legacy.write_text(json.dumps(original), encoding="utf-8")
    repo = SQLiteRankCountersRepository(tmp_path / "stats.db")
    repo.initialize(legacy)
    assert repo.get_user("-1", "2", TODAY)["total"] == 123
    repo.increment("-1", "2", TODAY)
    legacy.write_text("broken", encoding="utf-8")
    reopened = SQLiteRankCountersRepository(repo.path)
    reopened.initialize(legacy)
    assert reopened.get_user("-1", "2", TODAY)["total"] == 124


@pytest.mark.parametrize("payload", [[], {"-1": []}, {"-1": {"2": {"total": -1}}},
                                     {"-1": {"2": {"total": True}}},
                                     {"-1": {"2": {"total": 7}, "3": {"total": -1}}},
                                     {"-1": {"2": {"last_daily_reset": "broken"}}}])
def test_invalid_migration_is_atomic_and_can_be_retried(tmp_path, payload):
    repo = SQLiteRankCountersRepository(tmp_path / "stats.db")
    legacy = tmp_path / "stats.json"
    legacy.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        repo.initialize(legacy)
    legacy.write_text('{"-1": {"2": {"total": 7}}}', encoding="utf-8")
    repo.initialize(legacy)
    assert repo.get_user("-1", "2", date.today())["total"] == 7


def test_concurrent_increments_survive_restart_without_lost_updates(repo, tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: repo.increment("-1", "2", TODAY), range(80)))
    assert sorted(row["total"] for row in results) == list(range(1, 81))
    reopened = SQLiteRankCountersRepository(repo.path)
    reopened.initialize(tmp_path / "missing.json")
    assert reopened.get_user("-1", "2", TODAY)["total"] == 80
    assert reopened.get_chat("-2", TODAY) == {}


def test_daily_and_weekly_rollover_on_read_and_write(repo):
    repo.increment("-1", "2", TODAY)
    tomorrow = date(2026, 9, 6)
    stats = repo.get_user("-1", "2", tomorrow)
    assert (stats["total"], stats["daily"], stats["weekly"]) == (1, 0, 1)
    stats = repo.increment("-1", "2", tomorrow)
    assert (stats["total"], stats["daily"], stats["weekly"]) == (2, 1, 2)
    week_later = date(2026, 9, 12)
    assert repo.get_chat("-1", week_later)["2"]["weekly"] == 0
    stats = repo.increment("-1", "2", week_later)
    assert (stats["total"], stats["daily"], stats["weekly"]) == (3, 1, 1)


def test_failed_rank_notification_keeps_committed_count(repo, monkeypatch):
    from tests import test_smoke_imports
    del test_smoke_imports
    import features.stat_rank_settings as feature

    monkeypatch.setattr(feature, "_counter_repository", repo)
    monkeypatch.setattr(feature, "RANKS", {1: "first"})
    monkeypatch.setattr(feature, "rank_notifications_disabled_chats", set())

    async def reply(text):
        assert repo.get_user("-1", "2", date.today())["total"] == 1
        raise RuntimeError("Telegram unavailable")

    message = SimpleNamespace(chat=SimpleNamespace(id=-1),
                              from_user=SimpleNamespace(id=2, is_bot=False), reply=reply)
    asyncio.run(feature.track_message_statistics(message))
    assert repo.get_user("-1", "2", date.today())["total"] == 1


def test_slow_notification_does_not_block_other_chats(repo, monkeypatch):
    from tests import test_smoke_imports
    del test_smoke_imports
    import features.stat_rank_settings as feature

    monkeypatch.setattr(feature, "_counter_repository", repo)
    monkeypatch.setattr(feature, "RANKS", {1: "first"})
    monkeypatch.setattr(feature, "rank_notifications_disabled_chats", {"-2"})

    async def scenario():
        waiting, release = asyncio.Event(), asyncio.Event()

        async def reply(text):
            waiting.set()
            await release.wait()

        def message(chat_id):
            return SimpleNamespace(chat=SimpleNamespace(id=chat_id),
                                   from_user=SimpleNamespace(id=2, is_bot=False), reply=reply)

        task = asyncio.create_task(feature.track_message_statistics(message(-1)))
        try:
            await asyncio.wait_for(waiting.wait(), 3)
            await asyncio.wait_for(feature.track_message_statistics(message(-2)), 3)
            assert repo.get_user("-2", "2", date.today())["total"] == 1
        finally:
            release.set()
            await task

    asyncio.run(scenario())


def test_rollback_handoff_and_reupgrade_include_legacy_writes(repo, tmp_path):
    legacy = tmp_path / "message_stats.json"
    repo.increment("-1", "2", TODAY)
    assert repo.handoff_to_legacy(legacy)
    data = json.loads(legacy.read_text(encoding="utf-8"))
    data["-1"]["2"]["total"] += 10  # old bot runs after rollback
    legacy.write_text(json.dumps(data), encoding="utf-8")
    assert not repo.handoff_to_legacy(legacy)  # do not overwrite old bot's progress
    repo.initialize(legacy)
    assert repo.get_user("-1", "2", TODAY)["total"] == 11
    repo.increment("-1", "2", TODAY)
    repo.initialize(legacy)
    assert repo.get_user("-1", "2", TODAY)["total"] == 12


def test_handoff_failure_keeps_sqlite_authoritative(repo, tmp_path, monkeypatch):
    from core.json_repository import JsonFileRepository

    repo.increment("-1", "2", TODAY)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(JsonFileRepository, "save", fail)
    with pytest.raises(OSError):
        repo.handoff_to_legacy(tmp_path / "stats.json")
    repo.initialize(tmp_path / "missing.json")
    assert repo.get_user("-1", "2", TODAY)["total"] == 1


def test_missing_json_after_handoff_does_not_erase_sqlite(repo, tmp_path):
    legacy = tmp_path / "stats.json"
    repo.increment("-1", "2", TODAY)
    repo.handoff_to_legacy(legacy)
    legacy.unlink()
    with pytest.raises(FileNotFoundError):
        repo.initialize(legacy)
    assert repo.get_user("-1", "2", TODAY)["total"] == 1


def test_export_script_works_without_installed_dependencies(repo, tmp_path):
    repo.increment("-1", "2", TODAY)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-S", str(root / "scripts/export_rank_counters.py"),
         "--app-dir", str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True, text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "message_stats.json").read_text())["-1"]["2"]["total"] == 1


def test_reports_read_sqlite_and_keep_rank_and_chat_boundaries(repo, monkeypatch):
    from tests import test_smoke_imports
    del test_smoke_imports
    import features.stat_rank_settings as feature

    monkeypatch.setattr(feature, "_counter_repository", repo)
    monkeypatch.setattr(feature, "RANKS", {1: "first", 2: "second"})

    async def name(chat_id, user_id):
        return f"user-{user_id}"

    monkeypatch.setattr(feature, "get_user_display_name", name)
    repo.increment("-1", "2", date.today())
    repo.increment("-1", "2", date.today())
    repo.increment("-2", "3", date.today())

    async def scenario():
        text, exists = await feature.get_user_statistics("-1", "2")
        assert exists and "Всего: 2" in text and "Ранг: second" in text
        assert (await feature.get_user_statistics("-1", "3"))[1] is False
        report = await feature.generate_chat_stats_report("-1")
        assert "user-2 - 2" in report and "user-3" not in report

    asyncio.run(scenario())
