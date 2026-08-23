import ast
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from tests import test_smoke_imports  # noqa: F401


class MemoryRepository:
    def __init__(self, value=None):
        self.value = value
        self.saved = None

    def load(self):
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value

    def save(self, value):
        self.saved = value


def test_rank_json_state_preserves_shared_object_identity():
    import features.stat_rank_settings as feature

    stats_identity = id(feature.message_stats)
    ranks_identity = id(feature.rank_notifications_disabled_chats)

    feature.load_stats(MemoryRepository({"100": {"200": {"total": 7}}}))
    feature.load_rank_notifications_settings(MemoryRepository({"disabled_chats": ["100", "300"]}))

    assert id(feature.message_stats) == stats_identity
    assert id(feature.rank_notifications_disabled_chats) == ranks_identity
    assert feature.message_stats["100"]["200"]["total"] == 7
    assert feature.rank_notifications_disabled_chats == {"100", "300"}

    stats_repo = MemoryRepository()
    rank_repo = MemoryRepository()
    feature.save_stats(stats_repo)
    feature.save_rank_notifications_settings(rank_repo)

    assert stats_repo.saved is feature.message_stats
    assert set(rank_repo.saved["disabled_chats"]) == {"100", "300"}


def test_sms_and_antispam_json_state_preserve_shared_set_identity():
    import features.content_filter as content_filter
    import features.sms_settings as sms_settings

    sms_identity = id(sms_settings.sms_disabled_chats)
    antispam_identity = id(content_filter.ANTISPAM_ENABLED_CHATS)

    sms_settings.load_sms_disabled_chats(MemoryRepository(["-1", "-2"]))
    content_filter.load_antispam_settings(MemoryRepository([-10, "-20"]))

    assert id(sms_settings.sms_disabled_chats) == sms_identity
    assert id(content_filter.ANTISPAM_ENABLED_CHATS) == antispam_identity
    assert sms_settings.sms_disabled_chats == {"-1", "-2"}
    assert content_filter.ANTISPAM_ENABLED_CHATS == {-10, "-20"}

    sms_repo = MemoryRepository()
    antispam_repo = MemoryRepository()
    sms_settings.save_sms_disabled_chats(sms_repo)
    content_filter.save_antispam_settings(antispam_repo)

    assert set(sms_repo.saved) == {"-1", "-2"}
    assert set(antispam_repo.saved) == {-10, "-20"}


def test_missing_or_invalid_json_clears_shared_state():
    import features.content_filter as content_filter
    import features.sms_settings as sms_settings
    import features.stat_rank_settings as stat_rank

    sms_settings.sms_disabled_chats.add("stale")
    sms_settings.load_sms_disabled_chats(MemoryRepository(FileNotFoundError()))
    assert not sms_settings.sms_disabled_chats

    content_filter.ANTISPAM_ENABLED_CHATS.add("stale")
    content_filter.load_antispam_settings(MemoryRepository({"not": "a list"}))
    assert not content_filter.ANTISPAM_ENABLED_CHATS

    stat_rank.message_stats["stale"] = {}
    stat_rank.load_stats(MemoryRepository([]))
    assert not stat_rank.message_stats


def test_message_stats_concurrent_updates_persist_in_order(monkeypatch):
    import features.stat_rank_settings as feature

    class DelayedRepository:
        def __init__(self):
            self.saved_totals = []

        def save(self, value):
            total = value["123"]["456"]["total"]
            if total == 1:
                time.sleep(0.03)
            self.saved_totals.append(total)

    repo = DelayedRepository()
    monkeypatch.setattr(feature, "_stats_repository", lambda: repo)
    feature.message_stats.clear()
    feature.rank_notifications_disabled_chats.clear()

    async def reply(_text):
        return None

    def message():
        return SimpleNamespace(
            chat=SimpleNamespace(id=123),
            from_user=SimpleNamespace(id=456),
            reply=reply,
        )

    async def run():
        await asyncio.gather(
            feature.track_message_statistics(message()),
            feature.track_message_statistics(message()),
        )

    asyncio.run(run())

    assert feature.message_stats["123"]["456"]["total"] == 2
    assert repo.saved_totals == [1, 2]


def _top_level_called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if isinstance(func, ast.Name):
            names.add(func.id)
    return names


def test_persistent_feature_modules_do_not_load_state_at_import_time():
    root = Path(__file__).resolve().parents[1]
    forbidden_by_file = {
        "features/stat_rank_settings.py": {
            "load_stats",
            "load_rank_notifications_settings",
            "load_stat_rank_state",
        },
        "features/sms_settings.py": {"load_sms_disabled_chats"},
        "features/content_filter.py": {"load_antispam_settings"},
    }

    for relative_path, forbidden in forbidden_by_file.items():
        called = _top_level_called_names(root / relative_path)
        assert called.isdisjoint(forbidden), f"import-time persistence in {relative_path}: {called & forbidden}"


def test_statistics_feature_contains_no_sqlite_calls():
    root = Path(__file__).resolve().parents[1]
    source = (root / "features/statistics.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert "sqlite3" not in imported_modules
    assert "asyncio.to_thread" in source


def test_sqlite_statistics_repository_preserves_schema_and_aggregations(tmp_path):
    from infrastructure.persistence.sqlite_statistics import SQLiteStatisticsRepository

    repo = SQLiteStatisticsRepository(tmp_path / "statistics.db")
    repo.init_schema()

    repo.log_message(-100, 1, "text", False, "Group A", "Alice", "alice")
    repo.log_message(-100, 2, "photo", False, "Group A", "Bob", None)
    repo.log_message(500, 3, "text", True, None, "Carol", "carol")
    repo.log_model_request(-100, 1, "gemini-test", "dialog")

    stats = repo.get_stats()
    assert stats["groups"] == {"Group A": 2}
    assert stats["private"] == {"Carol (@carol)": 1}
    assert stats["model_usage"] == {"Group A": 1}
    assert sum(repo.get_activity_by_hour().values()) == 3


def test_async_statistics_facade_delegates_to_repository(monkeypatch):
    import features.statistics as statistics

    calls = []

    class FakeStatisticsRepository:
        def log_message(self, *args):
            calls.append(("log", args))

        def get_stats(self, period_hours=None):
            calls.append(("stats", period_hours))
            return {"groups": {}, "private": {}, "model_usage": {}}

        def get_activity_by_hour(self, period_hours=None):
            calls.append(("activity", period_hours))
            return {hour: 0 for hour in range(24)}

    monkeypatch.setattr(statistics, "_statistics_repository", FakeStatisticsRepository())

    asyncio.run(statistics.log_message(1, 2, "text", False, "chat", "user", None))
    asyncio.run(statistics.get_messages_last_24_hours())
    asyncio.run(statistics.get_activity_by_hour(48))

    assert calls[0][0] == "log"
    assert ("stats", 24) in calls
    assert ("activity", 48) in calls
