"""Управление фоновыми asyncio-задачами приложения."""

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any


TaskFactory = Callable[[], Coroutine[Any, Any, Any]]


class TaskSupervisor:
    """Владеет фоновыми задачами и восстанавливает явно помеченные scheduler-loop'ы."""

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger(__name__)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._recovering_names: set[str] = set()
        self._stopping = False

    @property
    def task_count(self) -> int:
        return len(self._tasks)

    @property
    def task_names(self) -> tuple[str, ...]:
        return tuple(sorted(task.get_name() for task in self._tasks if not task.done()))

    @property
    def recovering_task_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._recovering_names))

    def start(self, coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
        if self._stopping:
            coro.close()
            raise RuntimeError("TaskSupervisor is stopping")
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def start_resilient(
        self,
        factory: TaskFactory,
        *,
        name: str,
        restart_delay_seconds: float = 1.0,
        max_restart_delay_seconds: float = 60.0,
        reset_backoff_after_seconds: float = 60.0,
    ) -> asyncio.Task[Any]:
        """Запустить долгоживущую задачу и перезапускать её после сбоя/раннего выхода."""
        if restart_delay_seconds <= 0:
            raise ValueError("restart_delay_seconds must be positive")
        if max_restart_delay_seconds < restart_delay_seconds:
            raise ValueError(
                "max_restart_delay_seconds must be >= restart_delay_seconds"
            )
        if reset_backoff_after_seconds <= 0:
            raise ValueError("reset_backoff_after_seconds must be positive")

        return self.start(
            self._run_resilient(
                factory,
                name=name,
                restart_delay_seconds=restart_delay_seconds,
                max_restart_delay_seconds=max_restart_delay_seconds,
                reset_backoff_after_seconds=reset_backoff_after_seconds,
            ),
            name=name,
        )

    async def _run_resilient(
        self,
        factory: TaskFactory,
        *,
        name: str,
        restart_delay_seconds: float,
        max_restart_delay_seconds: float,
        reset_backoff_after_seconds: float,
    ) -> None:
        failures = 0

        while not self._stopping:
            self._recovering_names.discard(name)
            started = time.monotonic()
            try:
                await factory()
            except asyncio.CancelledError:
                raise
            except Exception:
                runtime = time.monotonic() - started
                if runtime >= reset_backoff_after_seconds:
                    failures = 0
                failures += 1
                delay = min(
                    max_restart_delay_seconds,
                    restart_delay_seconds * (2 ** min(failures - 1, 10)),
                )
                self._recovering_names.add(name)
                self._logger.exception(
                    "Background task %s crashed; restarting in %.1fs (failure=%d)",
                    name,
                    delay,
                    failures,
                )
            else:
                if self._stopping:
                    return
                runtime = time.monotonic() - started
                if runtime >= reset_backoff_after_seconds:
                    failures = 0
                failures += 1
                delay = min(
                    max_restart_delay_seconds,
                    restart_delay_seconds * (2 ** min(failures - 1, 10)),
                )
                self._recovering_names.add(name)
                self._logger.warning(
                    "Background task %s exited unexpectedly; restarting in %.1fs "
                    "(failure=%d)",
                    name,
                    delay,
                    failures,
                )

            if self._stopping:
                self._recovering_names.discard(name)
                return

            try:
                await asyncio.sleep(delay)
            finally:
                if self._stopping:
                    self._recovering_names.discard(name)

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        self._recovering_names.discard(task.get_name())
        if task.cancelled():
            return

        try:
            task.result()
        except Exception:
            self._logger.exception("Background task %s crashed", task.get_name())

    async def stop(self) -> None:
        self._stopping = True
        tasks = tuple(self._tasks)
        if not tasks:
            self._recovering_names.clear()
            return

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._recovering_names.clear()
