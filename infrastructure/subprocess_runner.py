"""Единый безопасный запуск внешних процессов (ffmpeg, ffprobe, lottie, sherlock).

Каждый запуск ограничен по времени. При таймауте и при отмене задачи процесс
(на POSIX — вся его группа) получает SIGKILL, а мы дожидаемся его выхода, чтобы
не оставлять зависших детей. Захватываемый вывод ограничен по объёму: stdout
хранит начало (его разбирают вызывающие), stderr — конец (там итоговая ошибка).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from typing import Sequence


logger = logging.getLogger(__name__)

DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024
KILL_GRACE_SECONDS = 5.0
_READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class SubprocessResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    duration_seconds: float
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.returncode == 0

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")


class _BoundedBuffer:
    def __init__(self, limit: int, *, keep_tail: bool) -> None:
        self._limit = limit
        self._keep_tail = keep_tail
        self._data = bytearray()
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        if self._keep_tail:
            self._data += chunk
            overflow = len(self._data) - self._limit
            if overflow > 0:
                del self._data[:overflow]
                self.truncated = True
            return
        free = self._limit - len(self._data)
        if len(chunk) > free:
            self.truncated = True
        if free > 0:
            self._data += chunk[:free]

    def getvalue(self) -> bytes:
        return bytes(self._data)


async def _drain(stream: asyncio.StreamReader | None, buffer: _BoundedBuffer) -> None:
    # Читаем до EOF даже сверх лимита: иначе заполненный pipe заблокирует процесс.
    if stream is None:
        return
    while True:
        chunk = await stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            return
        buffer.append(chunk)


def kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Немедленно завершить процесс, а на POSIX — всю его группу."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    except OSError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _consume_exceptions(tasks: set[asyncio.Future]) -> None:
    for task in tasks:
        if task.done() and not task.cancelled() and task.exception() is not None:
            logger.warning("[subprocess] output reader failed: %r", task.exception())


async def _reap(proc: asyncio.subprocess.Process, tasks: set[asyncio.Future]) -> None:
    kill_process_tree(proc)
    _done, pending = await asyncio.wait(tasks, timeout=KILL_GRACE_SECONDS)
    if pending:
        # Pipe держит потомок, сбежавший из группы: не ждём его бесконечно.
        logger.warning("[subprocess] pid=%s did not release pipes after kill", proc.pid)
        for task in pending:
            task.cancel()
    _consume_exceptions(tasks)


def _describe(command: Sequence[str]) -> str:
    # В лог попадают только программа и первые аргументы, без пользовательских данных.
    head = [os.path.basename(str(command[0])), *map(str, command[1:3])]
    return " ".join(head)


async def run_subprocess(
    command: Sequence[str],
    *,
    timeout: float,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> SubprocessResult:
    """Запустить команду без shell и вернуть результат.

    Таймаут обязателен. По таймауту процесс убивается и возвращается результат
    с ``timed_out=True``. ``asyncio.CancelledError`` пробрасывается дальше —
    после того как процесс убит и завершился.
    """
    if timeout is None or timeout <= 0:
        raise ValueError("timeout must be a positive number of seconds")
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive")
    if not command:
        raise ValueError("command must not be empty")

    process_kwargs = {
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if os.name == "posix":
        process_kwargs["start_new_session"] = True

    started = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(*command, **process_kwargs)
    stdout_buffer = _BoundedBuffer(max_output_bytes, keep_tail=False)
    stderr_buffer = _BoundedBuffer(max_output_bytes, keep_tail=True)
    tasks: set[asyncio.Future] = {
        asyncio.ensure_future(_drain(proc.stdout, stdout_buffer)),
        asyncio.ensure_future(_drain(proc.stderr, stderr_buffer)),
        asyncio.ensure_future(proc.wait()),
    }

    timed_out = False
    try:
        _done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            timed_out = True
            logger.warning(
                "[subprocess] timeout after %.1fs, killing pid=%s: %s",
                timeout,
                proc.pid,
                _describe(command),
            )
            await _reap(proc, tasks)
        else:
            _consume_exceptions(tasks)
    except asyncio.CancelledError:
        # shield: повторная отмена не должна оставить процесс неубранным.
        await asyncio.shield(_reap(proc, tasks))
        raise

    return SubprocessResult(
        returncode=proc.returncode,
        stdout=stdout_buffer.getvalue(),
        stderr=stderr_buffer.getvalue(),
        timed_out=timed_out,
        duration_seconds=time.perf_counter() - started,
        stdout_truncated=stdout_buffer.truncated,
        stderr_truncated=stderr_buffer.truncated,
    )


def find_venv_executable(name: str) -> str | None:
    """Найти консольный скрипт рядом с текущим интерпретатором, затем в PATH.

    Скрипты пакетов из venv лежат рядом с ``sys.executable`` (``bin/`` или
    ``Scripts/``), поэтому путь не зависит от того, где развёрнут бот.
    """
    if sys.executable:
        candidate = os.path.join(os.path.dirname(sys.executable), name)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which(name)
