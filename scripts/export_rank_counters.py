"""Hand current counters back to JSON before rollback to a legacy release.

The caller must stop the bot first. SQLite data is retained; a migration marker
ensures the next upgrade imports messages counted by the old release as well.
"""

import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.app_dir.resolve()))
    try:
        from infrastructure.persistence.sqlite_rank_counters import SQLiteRankCountersRepository

        exported = SQLiteRankCountersRepository(args.app_dir / "statistics.db").handoff_to_legacy(
            args.app_dir / "message_stats.json"
        )
        print("Rank counters exported for legacy rollback" if exported else "No counter migration to export")
    finally:
        sys.path.pop(0)


if __name__ == "__main__":
    main()
