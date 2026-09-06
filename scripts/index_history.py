"""Pre-index a running legacy bot's journal before switching production code.

The last record is intentionally left open until startup under the new version:
the live writer might still be appending its multiline text.
"""

import argparse
import importlib.util
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--repository-module", type=Path)
    args = parser.parse_args()
    module_path = args.repository_module or args.app_dir / "infrastructure/persistence/sqlite_history.py"
    spec = importlib.util.spec_from_file_location("history_index_adapter", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    print("Preparing history index; production code has not been switched", flush=True)
    module.SQLiteHistoryRepository(args.app_dir / "history.db", args.app_dir / "user_messages.log").initialize(
        finalize_tail=False,
    )
    print("History index prepared; the final journal tail will be imported at startup", flush=True)


if __name__ == "__main__":
    main()
