#!/usr/bin/env python3
"""Create an online, append-only backup of Upupa runtime state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3


LABEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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


def create_backup(source_dir: Path, destination_root: Path, label: str) -> Path:
    source_dir = source_dir.resolve()
    destination_root = destination_root.resolve()
    if not LABEL_RE.fullmatch(label):
        raise ValueError("backup label contains unsupported characters")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir = destination_root / f"{stamp}-{label}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    candidates = sorted(
        {
            *source_dir.glob("*.db"),
            *source_dir.glob("*.json"),
            source_dir / "user_messages.log",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()

    backup_dir = create_backup(args.source, args.destination, args.label)
    print(backup_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
