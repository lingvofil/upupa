#!/usr/bin/env python3
"""Create a bounded online backup of Upupa runtime state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import errno
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3


LABEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")
BACKUP_DIR_RE = re.compile(
    r"^\d{8}T\d{6}\.\d{6}Z-[A-Za-z0-9._-]+$"
)
DEFAULT_BACKUPS_TO_KEEP = 3


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_sqlite(source: Path, target: Path) -> None:
    with sqlite3.connect(source, timeout=30) as source_conn:
        with sqlite3.connect(target) as target_conn:
            source_conn.backup(target_conn)


def _completed_backups(destination_root: Path) -> list[Path]:
    if not destination_root.exists():
        return []
    return sorted(
        child
        for child in destination_root.iterdir()
        if child.is_dir()
        and BACKUP_DIR_RE.fullmatch(child.name)
        and (child / "manifest.json").is_file()
    )


def prune_backups(destination_root: Path, *, keep_completed: int) -> list[Path]:
    """Remove stale/incomplete deploy backups while preserving recent complete ones."""
    if keep_completed < 0:
        raise ValueError("keep_completed must be non-negative")
    destination_root.mkdir(parents=True, exist_ok=True)
    removed: list[Path] = []

    # Official deploys are serialized by the workflow concurrency group, so a
    # matching directory without a manifest is a failed/incomplete backup.
    for child in list(destination_root.iterdir()):
        if (
            child.is_dir()
            and BACKUP_DIR_RE.fullmatch(child.name)
            and not (child / "manifest.json").is_file()
        ):
            shutil.rmtree(child)
            removed.append(child)

    completed = _completed_backups(destination_root)
    stale = completed[:-keep_completed] if keep_completed else completed
    for child in stale:
        shutil.rmtree(child)
        removed.append(child)
    return removed


def _is_disk_full(exc: BaseException) -> bool:
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return True
    message = str(exc).casefold()
    return "disk is full" in message or "no space left on device" in message


def _create_backup_once(
    source_dir: Path,
    destination_root: Path,
    label: str,
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir = destination_root / f"{stamp}-{label}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    try:
        candidates = sorted(
            {
                *source_dir.glob("*.db"),
                *source_dir.glob("*.json"),
                source_dir / "user_messages.log",
                source_dir / "history_deletions.jsonl",
            }
        )
        manifest_files = []
        for source in candidates:
            if not source.is_file():
                continue
            target = backup_dir / source.name
            if source.suffix == ".db":
                _backup_sqlite(source, target)
                kind = "sqlite"
            else:
                shutil.copy2(source, target)
                kind = "file"
            manifest_files.append(
                {
                    "name": source.name,
                    "kind": kind,
                    "size": target.stat().st_size,
                    "sha256": _sha256(target),
                }
            )

        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": label,
            "source": str(source_dir),
            "files": manifest_files,
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return backup_dir
    except BaseException:
        # A failed copy must not make disk pressure worse for the next deploy.
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise


def create_backup(
    source_dir: Path,
    destination_root: Path,
    label: str,
    *,
    keep: int = DEFAULT_BACKUPS_TO_KEEP,
) -> Path:
    source_dir = source_dir.resolve()
    destination_root = destination_root.resolve()
    if not LABEL_RE.fullmatch(label):
        raise ValueError("backup label contains unsupported characters")
    if keep < 1:
        raise ValueError("at least one completed backup must be retained")

    # Keep room for the new snapshot. Never delete the last known-good backup
    # merely to make a new one; if one old backup is still too large, fail safe.
    retain_before = max(1, keep - 1)
    prune_backups(destination_root, keep_completed=retain_before)

    try:
        backup_dir = _create_backup_once(source_dir, destination_root, label)
    except BaseException as exc:
        completed = _completed_backups(destination_root)
        if not _is_disk_full(exc) or len(completed) <= 1:
            raise
        prune_backups(destination_root, keep_completed=1)
        backup_dir = _create_backup_once(source_dir, destination_root, label)

    prune_backups(destination_root, keep_completed=keep)
    return backup_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--keep", type=int, default=DEFAULT_BACKUPS_TO_KEEP)
    args = parser.parse_args()

    backup_dir = create_backup(
        args.source,
        args.destination,
        args.label,
        keep=args.keep,
    )
    print(backup_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
