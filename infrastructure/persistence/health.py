"""Bounded probes of existing runtime databases (never create missing files)."""

from contextlib import closing
from pathlib import Path
import sqlite3


def check_databases(statistics_path: Path, world_path: Path, history_path: Path | None = None) -> None:
    databases = [
        (statistics_path, ("message_stats", "rank_counters")),
        (world_path, ("world_states",)),
    ]
    if history_path is not None:
        databases.append((history_path, ("history_messages", "history_checkpoint", "history_fts")))
    for path, tables in databases:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw",
                                     uri=True, timeout=1)) as conn:
            # Test write-lock availability without changing application data.
            conn.execute("BEGIN IMMEDIATE")
            try:
                for table in tables:
                    conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            finally:
                conn.rollback()
