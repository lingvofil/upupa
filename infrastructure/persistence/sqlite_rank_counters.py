"""Transactional message counters; legacy JSON is imported exactly once."""

from contextlib import closing
from datetime import date
from pathlib import Path
import sqlite3

from core.json_repository import JsonFileRepository


MIGRATION = "rank-counters:001-json-import"
LEGACY_HANDOFF = "rank-counters:legacy-active"


class SQLiteRankCountersRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def initialize(self, legacy_path: str | Path) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""CREATE TABLE IF NOT EXISTS rank_counters (
                chat_id TEXT NOT NULL, user_id TEXT NOT NULL,
                total INTEGER NOT NULL CHECK(total >= 0),
                daily INTEGER NOT NULL CHECK(daily >= 0),
                weekly INTEGER NOT NULL CHECK(weekly >= 0),
                last_daily_reset TEXT NOT NULL, last_weekly_reset TEXT NOT NULL,
                PRIMARY KEY(chat_id, user_id)
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS persistence_migrations (
                migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL
            )""")
            handed_back = conn.execute("SELECT 1 FROM persistence_migrations WHERE migration_id=?",
                                       (LEGACY_HANDOFF,)).fetchone()
            if handed_back:
                # An older release has been writing JSON since an explicit rollback.
                # Re-import its current snapshot in the same transaction.
                conn.execute("DELETE FROM rank_counters")
                conn.execute("DELETE FROM persistence_migrations WHERE migration_id IN (?, ?)",
                             (MIGRATION, LEGACY_HANDOFF))
            if conn.execute("SELECT 1 FROM persistence_migrations WHERE migration_id=?",
                            (MIGRATION,)).fetchone():
                return
            try:
                payload = JsonFileRepository(legacy_path).load()
            except FileNotFoundError:
                if handed_back:
                    raise
                payload = {}
            if not isinstance(payload, dict):
                raise ValueError("Legacy message statistics must be an object")
            today = date.today().isoformat()
            for chat_id, users in payload.items():
                if not isinstance(users, dict):
                    raise ValueError("Legacy chat statistics must be an object")
                for user_id, stats in users.items():
                    if not isinstance(stats, dict):
                        raise ValueError("Legacy user statistics must be an object")
                    counts = [stats.get(key, 0) for key in ("total", "daily", "weekly")]
                    if any(type(value) is not int or value < 0 for value in counts):
                        raise ValueError("Legacy counters must be nonnegative integers")
                    dates = [stats.get(key, today) for key in
                             ("last_daily_reset", "last_weekly_reset")]
                    for value in dates:
                        date.fromisoformat(value)
                    # A pre-existing row without a marker is ambiguous: fail rather
                    # than replacing newer counters or silently dropping legacy data.
                    conn.execute("INSERT INTO rank_counters VALUES (?, ?, ?, ?, ?, ?, ?)",
                                 (str(chat_id), str(user_id), *counts, *dates))
            conn.execute("INSERT INTO persistence_migrations VALUES (?, ?)", (MIGRATION, today))

    def handoff_to_legacy(self, legacy_path: str | Path) -> bool:
        """Called with the bot STOPPED, before rolling back to a JSON-only release."""
        if not self.path.exists():
            return False
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='persistence_migrations'").fetchone():
                return False
            if not conn.execute("SELECT 1 FROM persistence_migrations WHERE migration_id=?",
                                (MIGRATION,)).fetchone():
                return False
            if conn.execute("SELECT 1 FROM persistence_migrations WHERE migration_id=?",
                            (LEGACY_HANDOFF,)).fetchone():
                return False
            payload = {}
            for row in conn.execute("SELECT * FROM rank_counters"):
                stats = dict(row)
                chat_id, user_id = stats.pop("chat_id"), stats.pop("user_id")
                payload.setdefault(chat_id, {})[user_id] = stats
            JsonFileRepository(legacy_path).save(payload)
            conn.execute("INSERT OR REPLACE INTO persistence_migrations VALUES (?, ?)",
                         (LEGACY_HANDOFF, date.today().isoformat()))
        return True

    @staticmethod
    def _current(row, today: date) -> dict | None:
        if row is None:
            return None
        stats = dict(row)
        if today > date.fromisoformat(stats["last_daily_reset"]):
            stats["daily"] = 0
            stats["last_daily_reset"] = today.isoformat()
        if (today - date.fromisoformat(stats["last_weekly_reset"])).days >= 7:
            stats["weekly"] = 0
            stats["last_weekly_reset"] = today.isoformat()
        return stats

    def increment(self, chat_id: str, user_id: str, today: date) -> dict:
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM rank_counters WHERE chat_id=? AND user_id=?",
                               (str(chat_id), str(user_id))).fetchone()
            stats = self._current(row, today) or {
                "total": 0, "daily": 0, "weekly": 0,
                "last_daily_reset": today.isoformat(),
                "last_weekly_reset": today.isoformat(),
            }
            for key in ("total", "daily", "weekly"):
                stats[key] += 1
            conn.execute("""INSERT INTO rank_counters VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                total=excluded.total, daily=excluded.daily, weekly=excluded.weekly,
                last_daily_reset=excluded.last_daily_reset,
                last_weekly_reset=excluded.last_weekly_reset""",
                (str(chat_id), str(user_id), stats["total"], stats["daily"], stats["weekly"],
                 stats["last_daily_reset"], stats["last_weekly_reset"]))
        return stats

    def get_user(self, chat_id: str, user_id: str, today: date) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM rank_counters WHERE chat_id=? AND user_id=?",
                               (str(chat_id), str(user_id))).fetchone()
            return self._current(row, today)

    def get_chat(self, chat_id: str, today: date) -> dict:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM rank_counters WHERE chat_id=?",
                                (str(chat_id),)).fetchall()
            return {row["user_id"]: self._current(row, today) for row in rows}
