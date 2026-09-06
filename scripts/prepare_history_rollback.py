"""Flush pending history to the legacy journal with the bot already stopped."""

import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.app_dir.resolve()))
    try:
        from infrastructure.persistence.sqlite_history import SQLiteHistoryRepository

        SQLiteHistoryRepository(args.app_dir / "history.db", args.app_dir / "user_messages.log").recover_pending()
        print("History journal ready for rollback")
    finally:
        sys.path.pop(0)


if __name__ == "__main__":
    main()
