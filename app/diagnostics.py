"""Safe process-local diagnostics exposed only through the loopback HTTP server."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import shutil
import time

from core.paths import (
    DATA_DIR,
    HISTORY_DB_PATH,
    PROJECT_ROOT,
    STATISTICS_DB_PATH,
    USER_MESSAGES_LOG_PATH,
    WORLD_DB_PATH,
)
from infrastructure.ai.execution import get_ai_execution_snapshot


RESOURCE_SNAPSHOT_INTERVAL_SECONDS = 5 * 60
DELETION_LEDGER_PATH = DATA_DIR / "history_deletions.jsonl"
DEPLOY_BACKUPS_PATH = PROJECT_ROOT.parent / "upupa-backups"
_PROC_STATUS_PATH = Path("/proc/self/status")
_PROC_STAT_PATH = Path("/proc/self/stat")
_PROC_UPTIME_PATH = Path("/proc/uptime")
_PROC_MEMINFO_PATH = Path("/proc/meminfo")
_PROC_FD_PATH = Path("/proc/self/fd")


def _parse_kib_file(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    with path.open("r", encoding="utf-8") as stream:
        for raw_line in stream:
            key, separator, remainder = raw_line.partition(":")
            if not separator:
                continue
            fields = remainder.strip().split()
            if not fields:
                continue
            try:
                value = int(fields[0])
            except ValueError:
                continue
            if len(fields) > 1 and fields[1].casefold() == "kb":
                value *= 1024
            values[key] = value
    return values


def _process_uptime_seconds() -> float:
    """Return process uptime from Linux procfs without adding psutil."""
    system_uptime = float(_PROC_UPTIME_PATH.read_text(encoding="utf-8").split()[0])
    stat_text = _PROC_STAT_PATH.read_text(encoding="utf-8").strip()
    closing_paren = stat_text.rfind(")")
    if closing_paren < 0:
        raise RuntimeError("unexpected /proc/self/stat format")
    fields_after_comm = stat_text[closing_paren + 1 :].split()
    # fields_after_comm[0] is field 3 (state), so index 19 is field 22 (starttime).
    start_ticks = int(fields_after_comm[19])
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    return max(0.0, system_uptime - (start_ticks / ticks_per_second))


def _safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _allocated_bytes(stat_result: os.stat_result) -> int:
    blocks = getattr(stat_result, "st_blocks", None)
    if blocks is None:
        return stat_result.st_size
    return int(blocks) * 512


def _directory_usage(path: Path) -> dict[str, int]:
    """Return logical and unique physical usage, accounting for hard links."""
    if not path.exists():
        return {
            "logical_bytes": 0,
            "physical_bytes": 0,
            "file_count": 0,
            "snapshot_count": 0,
        }

    logical_bytes = 0
    physical_bytes = 0
    file_count = 0
    seen_inodes: set[tuple[int, int]] = set()
    snapshot_count = 0

    try:
        snapshot_count = sum(
            1
            for child in path.iterdir()
            if child.is_dir() and (child / "manifest.json").is_file()
        )
    except OSError:
        snapshot_count = 0

    for root, _dirs, files in os.walk(path):
        for name in files:
            member = Path(root) / name
            try:
                stat_result = member.stat()
            except OSError:
                continue
            file_count += 1
            logical_bytes += stat_result.st_size
            inode_key = (stat_result.st_dev, stat_result.st_ino)
            if inode_key not in seen_inodes:
                seen_inodes.add(inode_key)
                physical_bytes += _allocated_bytes(stat_result)

    return {
        "logical_bytes": logical_bytes,
        "physical_bytes": physical_bytes,
        "file_count": file_count,
        "snapshot_count": snapshot_count,
    }


def collect_resource_snapshot() -> dict:
    """Collect bounded Linux/process/storage metrics using procfs and stdlib only."""
    status = _parse_kib_file(_PROC_STATUS_PATH)
    meminfo = _parse_kib_file(_PROC_MEMINFO_PATH)
    memory_total = meminfo.get("MemTotal", 0)
    memory_available = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    disk = shutil.disk_usage(PROJECT_ROOT)

    try:
        open_fds = len(tuple(_PROC_FD_PATH.iterdir()))
    except OSError:
        open_fds = -1

    return {
        "process": {
            "rss_bytes": status.get("VmRSS", 0),
            "vm_swap_bytes": status.get("VmSwap", 0),
            "threads": status.get("Threads", 0),
            "open_fds": open_fds,
            "uptime_seconds": round(_process_uptime_seconds(), 1),
        },
        "system_memory": {
            "total_bytes": memory_total,
            "available_bytes": memory_available,
            "used_bytes": max(0, memory_total - memory_available),
        },
        "system_swap": {
            "total_bytes": swap_total,
            "free_bytes": swap_free,
            "used_bytes": max(0, swap_total - swap_free),
        },
        "filesystem": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
        },
        "state_files": {
            "user_messages_log_bytes": _safe_file_size(USER_MESSAGES_LOG_PATH),
            "history_db_bytes": _safe_file_size(HISTORY_DB_PATH),
            "statistics_db_bytes": _safe_file_size(STATISTICS_DB_PATH),
            "world_db_bytes": _safe_file_size(WORLD_DB_PATH),
            "deletion_ledger_bytes": _safe_file_size(DELETION_LEDGER_PATH),
        },
        "deploy_backups": _directory_usage(DEPLOY_BACKUPS_PATH),
    }


def build_runtime_diagnostics(supervisor) -> dict:
    """Return bounded, non-sensitive runtime counters for operational debugging."""
    return {
        "pid": os.getpid(),
        "resources": collect_resource_snapshot(),
        "ai": asdict(get_ai_execution_snapshot()),
        "background_tasks": {
            "active": list(supervisor.task_names),
            "recovering": list(getattr(supervisor, "recovering_task_names", ())),
            "restart_total": int(getattr(supervisor, "restart_count", 0)),
            "restart_counts": dict(getattr(supervisor, "restart_counts", {})),
        },
    }


async def resource_snapshot_loop(
    supervisor,
    *,
    interval_seconds: float = RESOURCE_SNAPSHOT_INTERVAL_SECONDS,
    logger: logging.Logger | None = None,
) -> None:
    """Periodically log a structured snapshot without blocking the event loop."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    resolved_logger = logger or logging.getLogger(__name__)

    while True:
        started = time.monotonic()
        try:
            payload = await asyncio.to_thread(build_runtime_diagnostics, supervisor)
            resolved_logger.info(
                "resource_snapshot=%s",
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            resolved_logger.exception("Failed to collect resource snapshot")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(0.0, interval_seconds - elapsed))


__all__ = [
    "RESOURCE_SNAPSHOT_INTERVAL_SECONDS",
    "build_runtime_diagnostics",
    "collect_resource_snapshot",
    "resource_snapshot_loop",
]
