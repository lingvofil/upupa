#!/usr/bin/env python3
"""Create a bounded online backup of Upupa runtime state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3


LABEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")
BACKUP_DIR_RE = re.compile(
    r"^\d{8}T\d{6}\.\d{6}Z-[A-Za-z0-9._-]+$"
)
DEFAULT_BACKUPS_TO_KEEP = 3
DEFAULT_JOURNAL_CHUNK_SIZE = 8 * 1024 * 1024
JOURNAL_NAME = "user_messages.log"
JOURNAL_PARTS_DIR = f"{JOURNAL_NAME}.parts"


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


def _safe_backup_member(backup_dir: Path, relative_name: str) -> Path | None:
    relative = Path(relative_name)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = backup_dir.resolve()
    candidate = (backup_dir / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _journal_reuse_index(
    destination_root: Path,
    *,
    chunk_size: int,
) -> dict[tuple[int, int, str], Path]:
    """Index verified-looking chunks from retained backups for hard-link reuse."""
    reusable: dict[tuple[int, int, str], Path] = {}
    for backup_dir in reversed(_completed_backups(destination_root)):
        try:
            manifest = json.loads(
                (backup_dir / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        for metadata in manifest.get("files", []):
            if (
                metadata.get("name") != JOURNAL_NAME
                or metadata.get("kind") != "chunked_file"
                or metadata.get("chunk_size") != chunk_size
            ):
                continue
            for chunk in metadata.get("chunks", []):
                try:
                    key = (
                        int(chunk["offset"]),
                        int(chunk["size"]),
                        str(chunk["sha256"]),
                    )
                    member = _safe_backup_member(
                        backup_dir,
                        str(chunk["path"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if (
                    member is not None
                    and member.is_file()
                    and member.stat().st_size == key[1]
                ):
                    reusable.setdefault(key, member)
    return reusable


def _read_exact(stream, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        data = stream.read(remaining)
        if not data:
            raise RuntimeError("history journal changed while backup was running")
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


def _backup_journal(
    source: Path,
    backup_dir: Path,
    destination_root: Path,
    *,
    chunk_size: int,
) -> dict:
    """Snapshot append-only journal as deduplicated immutable hard-linked chunks."""
    if chunk_size < 1:
        raise ValueError("journal chunk size must be positive")

    parts_dir = backup_dir / JOURNAL_PARTS_DIR
    parts_dir.mkdir()
    reusable = _journal_reuse_index(
        destination_root,
        chunk_size=chunk_size,
    )
    verified_reuse: dict[Path, bool] = {}

    whole_digest = hashlib.sha256()
    chunks = []
    with source.open("rb") as stream:
        snapshot_size = os.fstat(stream.fileno()).st_size
        offset = 0
        index = 0
        while offset < snapshot_size:
            size = min(chunk_size, snapshot_size - offset)
            data = _read_exact(stream, size)
            whole_digest.update(data)
            digest = hashlib.sha256(data).hexdigest()
            relative_name = (
                f"{JOURNAL_PARTS_DIR}/{index:08d}-{size:08x}-{digest}.chunk"
            )
            target = backup_dir / relative_name
            key = (offset, size, digest)
            previous = reusable.get(key)
            reused = False

            if previous is not None:
                valid = verified_reuse.get(previous)
                if valid is None:
                    valid = (
                        previous.stat().st_size == size
                        and _sha256(previous) == digest
                    )
                    verified_reuse[previous] = valid
                if valid:
                    os.link(previous, target)
                    reused = True

            if not reused:
                with target.open("xb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                target.chmod(0o444)

            chunks.append(
                {
                    "path": relative_name,
                    "offset": offset,
                    "size": size,
                    "sha256": digest,
                }
            )
            offset += size
            index += 1

    return {
        "name": JOURNAL_NAME,
        "kind": "chunked_file",
        "size": snapshot_size,
        "sha256": whole_digest.hexdigest(),
        "chunk_size": chunk_size,
        "chunks": chunks,
    }


def _create_backup_once(
    source_dir: Path,
    destination_root: Path,
    label: str,
    *,
    journal_chunk_size: int,
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir = destination_root / f"{stamp}-{label}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    try:
        candidates = sorted(
            {
                *source_dir.glob("*.db"),
                *source_dir.glob("*.json"),
                source_dir / JOURNAL_NAME,
                source_dir / "history_deletions.jsonl",
            }
        )
        manifest_files = []
        for source in candidates:
            if not source.is_file():
                continue
            if source.name == JOURNAL_NAME:
                manifest_files.append(
                    _backup_journal(
                        source,
                        backup_dir,
                        destination_root,
                        chunk_size=journal_chunk_size,
                    )
                )
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
    journal_chunk_size: int = DEFAULT_JOURNAL_CHUNK_SIZE,
) -> Path:
    source_dir = source_dir.resolve()
    destination_root = destination_root.resolve()
    if not LABEL_RE.fullmatch(label):
        raise ValueError("backup label contains unsupported characters")
    if keep < 1:
        raise ValueError("at least one completed backup must be retained")
    if journal_chunk_size < 1:
        raise ValueError("journal chunk size must be positive")

    # Keep room for the new snapshot. Never delete the last known-good backup
    # merely to make a new one; if one old backup is still too large, fail safe.
    retain_before = max(1, keep - 1)
    prune_backups(destination_root, keep_completed=retain_before)

    try:
        backup_dir = _create_backup_once(
            source_dir,
            destination_root,
            label,
            journal_chunk_size=journal_chunk_size,
        )
    except BaseException as exc:
        completed = _completed_backups(destination_root)
        if not _is_disk_full(exc) or len(completed) <= 1:
            raise
        prune_backups(destination_root, keep_completed=1)
        backup_dir = _create_backup_once(
            source_dir,
            destination_root,
            label,
            journal_chunk_size=journal_chunk_size,
        )

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
