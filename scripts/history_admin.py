#!/usr/bin/env python3
"""Administrative export, deletion and retention commands for indexed history.

Destructive operations never edit ``user_messages.log``. They append a durable
logical-deletion event to ``history_deletions.jsonl`` and then update SQLite.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

from core.settings import APP_TIMEZONE_NAME
from core.time_utils import configure_process_timezone, parse_history_datetime
from infrastructure.persistence.managed_history import ManagedHistoryRepository
from infrastructure.persistence.sqlite_history import SQLiteHistoryRepository


EXPORT_FIELDS = (
    "id",
    "timestamp",
    "timezone",
    "chat_id",
    "chat_title",
    "user_id",
    "username",
    "full_name",
    "text",
    "message_id",
)


def open_history(app_dir: Path) -> ManagedHistoryRepository:
    app_dir = app_dir.resolve()
    base = SQLiteHistoryRepository(
        app_dir / "history.db",
        app_dir / "user_messages.log",
    )
    base.initialize()
    return ManagedHistoryRepository(base, app_dir / "history_deletions.jsonl")


def export_history(
    repository: ManagedHistoryRepository,
    output: Path,
    *,
    format_name: str = "jsonl",
    chat_id: str | None = None,
    user_id: str | None = None,
    start=None,
    end=None,
) -> int:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0

    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = None
        if format_name == "csv":
            writer = csv.DictWriter(stream, fieldnames=EXPORT_FIELDS)
            writer.writeheader()
        elif format_name != "jsonl":
            raise ValueError(f"unsupported export format: {format_name}")

        def write_row(row):
            nonlocal count
            exported = {
                "id": row["id"],
                "timestamp": row["timestamp"],
                "timezone": APP_TIMEZONE_NAME,
                "chat_id": row["chat_id"],
                "chat_title": row["chat_title"],
                "user_id": row["user_id"],
                "username": row["username"],
                "full_name": row["full_name"],
                "text": row["text"],
                "message_id": row["message_id"],
            }
            if writer is not None:
                writer.writerow(exported)
            else:
                stream.write(json.dumps(exported, ensure_ascii=False) + "\n")
            count += 1

        repository.scan(
            chat_id,
            write_row,
            user_id=user_id,
            start=start,
            end=end,
        )
    return count


def _add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--chat-id")
    parser.add_argument("--user-id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=Path(__file__).resolve().parents[1])
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="export indexed history")
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--format", choices=("jsonl", "csv"), default="jsonl")
    _add_scope_arguments(export)
    export.add_argument("--start")
    export.add_argument("--end")

    delete = subparsers.add_parser("delete", help="durably delete a history scope")
    _add_scope_arguments(delete)
    delete.add_argument("--before")
    delete.add_argument("--yes", action="store_true")
    delete.add_argument("--vacuum", action="store_true")

    retain = subparsers.add_parser("retain", help="delete history older than N days")
    retain.add_argument("--days", required=True, type=int)
    retain.add_argument("--chat-id")
    retain.add_argument("--yes", action="store_true")
    retain.add_argument("--vacuum", action="store_true")

    subparsers.add_parser("ledger", help="print durable deletion ledger")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_process_timezone()
    args = build_parser().parse_args(argv)
    repository = open_history(args.app_dir)

    if args.command == "export":
        count = export_history(
            repository,
            args.output,
            format_name=args.format,
            chat_id=args.chat_id,
            user_id=args.user_id,
            start=parse_history_datetime(args.start) if args.start else None,
            end=parse_history_datetime(args.end) if args.end else None,
        )
        print(f"exported={count} timezone={APP_TIMEZONE_NAME} output={args.output}")
        return 0

    if args.command == "ledger":
        for event in repository.deletion_events():
            print(json.dumps(event, ensure_ascii=False, sort_keys=True))
        return 0

    if not args.yes:
        print("Refusing destructive history operation without --yes", file=sys.stderr)
        return 2

    if args.command == "delete":
        if args.chat_id is None and args.user_id is None and args.before is None:
            print("Specify --chat-id, --user-id and/or --before", file=sys.stderr)
            return 2
        deleted = repository.delete_history(
            chat_id=args.chat_id,
            user_id=args.user_id,
            before=parse_history_datetime(args.before) if args.before else None,
        )
    elif args.command == "retain":
        deleted = repository.prune_older_than(args.days, chat_id=args.chat_id)
    else:
        raise AssertionError(args.command)

    if args.vacuum:
        repository.compact()
    print(f"deleted={deleted} journal_untouched={repository.log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
