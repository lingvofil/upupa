#!/usr/bin/env python3
"""Recover one Crocodile chat scoreboard from append-only runtime backups.

The utility is intentionally conservative: it scans backups newest-first and
only accepts a candidate whose stored names and points match every expected
entry supplied by the operator. It replaces only the requested chat table,
preserves all other chats, writes atomically, and keeps a pre-recovery copy.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil


def _load_json_object(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _entry_points(value: object) -> int:
    if isinstance(value, int):
        return int(value)
    if isinstance(value, dict):
        return int(value.get("pts", 0))
    return 0


def _entry_name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name", "") or "")
    return ""


def parse_expected(items: list[str]) -> dict[str, int]:
    expected: dict[str, int] = {}
    for item in items:
        name, separator, raw_points = item.rpartition("=")
        if not separator or not name.strip():
            raise ValueError(f"expected NAME=POINTS, got {item!r}")
        points = int(raw_points)
        if points < 0:
            raise ValueError(f"points must be non-negative: {item!r}")
        expected[name] = points
    if not expected:
        raise ValueError("at least one --expected entry is required")
    return expected


def scoreboard_matches(table: object, expected: dict[str, int]) -> bool:
    if not isinstance(table, dict):
        return False

    actual: dict[str, set[int]] = {}
    for value in table.values():
        name = _entry_name(value)
        if not name:
            continue
        actual.setdefault(name, set()).add(_entry_points(value))

    return all(points in actual.get(name, set()) for name, points in expected.items())


def find_matching_backup(
    backup_root: Path,
    chat_id: str,
    expected: dict[str, int],
) -> tuple[Path, dict] | None:
    candidates = sorted(
        backup_root.glob("*/crocodile_scores.json"),
        key=lambda path: path.parent.name,
        reverse=True,
    )
    for path in candidates:
        try:
            payload = _load_json_object(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        table = payload.get(chat_id)
        if scoreboard_matches(table, expected):
            return path, table
    return None


def format_top(table: dict, limit: int = 10) -> str:
    entries = []
    for value in table.values():
        name = _entry_name(value) or "игрок"
        entries.append((name, _entry_points(value)))
    entries.sort(key=lambda item: (-item[1], item[0]))
    return "; ".join(f"{name}={points}" for name, points in entries[:limit])


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def recover_scoreboard(
    *,
    backup_root: Path,
    scores_file: Path,
    chat_id: str,
    expected: dict[str, int],
    marker_file: Path,
    apply: bool,
) -> Path:
    if marker_file.exists():
        print(f"recovery already applied: marker={marker_file}")
        return marker_file

    match = find_matching_backup(backup_root, chat_id, expected)
    if match is None:
        raise RuntimeError(
            f"no backup matches expected scoreboard for chat {chat_id}"
        )

    source_path, recovered_table = match
    print(f"selected_backup={source_path}")
    print(f"recovered_top={format_top(recovered_table)}")

    if not apply:
        print("dry-run: no files changed")
        return source_path

    if scores_file.is_file():
        current = _load_json_object(scores_file)
    else:
        current = {}

    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    before_path = backup_root / f"manual-crocodile-recovery-{stamp}-before.json"
    if scores_file.is_file():
        shutil.copy2(scores_file, before_path)
    else:
        _atomic_write_json(before_path, current)

    current[chat_id] = recovered_table
    _atomic_write_json(scores_file, current)

    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.write_text(
        json.dumps(
            {
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "chat_id": chat_id,
                "source": str(source_path),
                "pre_recovery_copy": str(before_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"pre_recovery_copy={before_path}")
    print(f"restored_scores_file={scores_file}")
    print(f"marker={marker_file}")
    return source_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--scores-file", required=True, type=Path)
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--expected", action="append", default=[])
    parser.add_argument("--marker", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    recover_scoreboard(
        backup_root=args.backup_root,
        scores_file=args.scores_file,
        chat_id=str(args.chat_id),
        expected=parse_expected(args.expected),
        marker_file=args.marker,
        apply=args.apply,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
