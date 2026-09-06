#!/usr/bin/env python3
"""Materialize a verified history journal from a deploy backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile


JOURNAL_NAME = "user_messages.log"


class BackupRestoreError(RuntimeError):
    """Backup manifest or payload is incomplete/corrupt."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(backup_dir: Path, relative_name: str) -> Path:
    relative = Path(relative_name)
    if relative.is_absolute() or ".." in relative.parts:
        raise BackupRestoreError("backup contains an unsafe member path")
    root = backup_dir.resolve()
    member = (backup_dir / relative).resolve()
    try:
        member.relative_to(root)
    except ValueError as exc:
        raise BackupRestoreError("backup member escapes backup directory") from exc
    return member


def _journal_metadata(backup_dir: Path) -> dict:
    manifest_path = backup_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise BackupRestoreError("backup manifest is unreadable") from exc
    for metadata in manifest.get("files", []):
        if metadata.get("name") == JOURNAL_NAME:
            return metadata
    raise BackupRestoreError("backup does not contain history journal metadata")


def _copy_verified_legacy_journal(
    backup_dir: Path,
    metadata: dict,
    output,
) -> tuple[int, str]:
    source = _safe_member(backup_dir, JOURNAL_NAME)
    if not source.is_file():
        raise BackupRestoreError("legacy backup journal is missing")
    expected_size = int(metadata["size"])
    expected_digest = str(metadata["sha256"])
    if source.stat().st_size != expected_size or _sha256(source) != expected_digest:
        raise BackupRestoreError("legacy backup journal failed verification")

    digest = hashlib.sha256()
    written = 0
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            output.write(chunk)
            digest.update(chunk)
            written += len(chunk)
    return written, digest.hexdigest()


def _copy_verified_chunked_journal(
    backup_dir: Path,
    metadata: dict,
    output,
) -> tuple[int, str]:
    expected_size = int(metadata["size"])
    expected_digest = str(metadata["sha256"])
    chunks = metadata.get("chunks")
    if not isinstance(chunks, list):
        raise BackupRestoreError("chunked journal metadata is invalid")

    digest = hashlib.sha256()
    written = 0
    for chunk in chunks:
        try:
            offset = int(chunk["offset"])
            size = int(chunk["size"])
            chunk_digest = str(chunk["sha256"])
            member = _safe_member(backup_dir, str(chunk["path"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupRestoreError("chunked journal metadata is invalid") from exc

        if offset != written:
            raise BackupRestoreError("journal chunks are not contiguous")
        if not member.is_file() or member.stat().st_size != size:
            raise BackupRestoreError("journal chunk is missing or has wrong size")
        if _sha256(member) != chunk_digest:
            raise BackupRestoreError("journal chunk failed verification")

        with member.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                output.write(block)
                digest.update(block)
                written += len(block)

    if written != expected_size or digest.hexdigest() != expected_digest:
        raise BackupRestoreError("materialized journal failed verification")
    return written, digest.hexdigest()


def restore_history_journal(
    backup_dir: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> Path:
    """Reconstruct and verify user_messages.log without changing the backup."""
    backup_dir = backup_dir.resolve()
    output_path = output_path.resolve()
    if output_path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing file: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    metadata = _journal_metadata(backup_dir)
    kind = metadata.get("kind")
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{output_path.name}.restore-",
            dir=output_path.parent,
            delete=False,
        ) as output:
            temporary_name = output.name
            if kind == "chunked_file":
                _copy_verified_chunked_journal(backup_dir, metadata, output)
            elif kind == "file":
                _copy_verified_legacy_journal(backup_dir, metadata, output)
            else:
                raise BackupRestoreError(f"unsupported journal backup kind: {kind!r}")
            output.flush()
            os.fsync(output.fileno())

        temporary = Path(temporary_name)
        if output_path.exists() and not force:
            raise FileExistsError(f"refusing to overwrite existing file: {output_path}")
        os.replace(temporary, output_path)
        return output_path
    except BaseException:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    restored = restore_history_journal(
        args.backup,
        args.output,
        force=args.force,
    )
    print(restored)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
