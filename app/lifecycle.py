"""Управление фоновыми asyncio-задачами приложения."""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any


class TaskSupervisor:
    """Владеет фоновыми задачами: хранит ссылки, логирует падения и отменяет их."""

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger(__name__)
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def task_count(self) -> int:
        return len(self._tasks)

    @property
    def task_names(self) -> tuple[str, ...]:
        return tuple(sorted(task.get_name() for task in self._tasks))

    def start(self, coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return

        try:
            task.result()
        except Exception:
            self._logger.exception("Background task %s crashed", task.get_name())

    async def stop(self) -> None:
        tasks = tuple(self._tasks)
        if not tasks:
            return

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
