"""Запуск Chromium с оглядкой на память сервиса.

Браузер занимает около 400 МБ внутри лимита systemd. Если запаса нет, лучше
отказать в одной команде, чем довести весь бот до зависания на нехватке памяти.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path


CHROMIUM_MIN_FREE_BYTES = 400 * 1024 * 1024
_PROC_CGROUP_PATH = Path("/proc/self/cgroup")
_PROC_MEMINFO_PATH = Path("/proc/meminfo")
_CGROUP_ROOT = Path("/sys/fs/cgroup")

_chromium_semaphore = asyncio.Semaphore(1)


class NotEnoughMemoryForChromium(RuntimeError):
    """Сейчас нельзя запускать браузер: памяти не хватит."""


def _service_memory_room() -> int | None:
    """Сколько ещё можно занять в лимите cgroup v2.

    Файловый кэш ядро вытеснит само, а своп считается занятым: вытесненное
    в него всё равно придётся вернуть в память.
    """
    for line in _PROC_CGROUP_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("0::"):
            cgroup_dir = _CGROUP_ROOT / line[3:].lstrip("/")
            break
    else:
        return None

    limit = (cgroup_dir / "memory.max").read_text(encoding="utf-8").strip()
    if limit == "max":
        return None

    used = int((cgroup_dir / "memory.current").read_text(encoding="utf-8"))
    for line in (cgroup_dir / "memory.stat").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(" ")
        if key in {"active_file", "inactive_file"}:
            used -= int(value)

    swap_path = cgroup_dir / "memory.swap.current"
    if swap_path.exists():
        used += int(swap_path.read_text(encoding="utf-8"))
    return int(limit) - used


def _system_memory_available() -> int | None:
    for line in _PROC_MEMINFO_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return None


def free_memory_bytes() -> int | None:
    """Вернуть запас памяти; None — если его негде узнать (например, не Linux)."""
    known = []
    for probe in (_service_memory_room, _system_memory_available):
        try:
            value = probe()
        except (OSError, ValueError):
            continue
        if value is not None:
            known.append(value)
    return min(known) if known else None


@asynccontextmanager
async def chromium_slot():
    """Не больше одного Chromium сразу и только при запасе памяти."""
    async with _chromium_semaphore:
        free = free_memory_bytes()
        if free is not None and free < CHROMIUM_MIN_FREE_BYTES:
            logging.warning(
                "Chromium не запущен: свободно %d МБ, нужно %d МБ",
                free // (1024 * 1024),
                CHROMIUM_MIN_FREE_BYTES // (1024 * 1024),
            )
            raise NotEnoughMemoryForChromium(
                "сейчас не хватает памяти на браузер, попробуй позже"
            )
        yield
