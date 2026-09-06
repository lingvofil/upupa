"""Loopback readiness served by the same event loop as Telegram polling."""

import asyncio
from dataclasses import dataclass
import os
import time

from aiohttp import web
from aiogram.methods import GetUpdates


@dataclass
class PollingHealth:
    last_success: float | None = None
    failed: bool = False
    stopping: bool = False

    async def observe(self, make_request, bot, method):
        if not isinstance(method, GetUpdates):
            return await make_request(bot, method)
        try:
            result = await make_request(bot, method)
        except Exception:
            self.failed = True
            raise
        self.last_success = time.monotonic()
        self.failed = False
        return result

    def ready(self) -> bool:
        return (not self.stopping and not self.failed and self.last_success is not None
                and time.monotonic() - self.last_success < 90)


class ReadinessServer:
    def __init__(self, polling, supervisor, database_probe, required_tasks, *, port=8766):
        self.polling = polling
        self.supervisor = supervisor
        self.database_probe = database_probe
        self.required_tasks = set(required_tasks)
        self.port = port
        self.runner = None

    async def handle(self, request):
        databases_ok = False
        try:
            await asyncio.wait_for(asyncio.to_thread(self.database_probe), timeout=3)
            databases_ok = True
        except Exception:
            pass
        active = set(self.supervisor.task_names)
        recovering = set(getattr(self.supervisor, "recovering_task_names", ()))
        missing = sorted(self.required_tasks - active)
        recovering_required = sorted(self.required_tasks & recovering)
        checks = {
            "polling": self.polling.ready(),
            "databases": databases_ok,
            "background_tasks": not missing and not recovering_required,
        }
        ok = all(checks.values())
        return web.json_response(
            {
                "ok": ok,
                "pid": os.getpid(),
                "checks": checks,
                "missing_tasks": missing,
                "recovering_tasks": recovering_required,
            },
            status=200 if ok else 503,
        )

    async def start(self):
        app = web.Application()
        app.router.add_get("/ready", self.handle)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        except BaseException:
            await self.runner.cleanup()
            raise

    async def stop(self):
        self.polling.stopping = True
        if self.runner is not None:
            await self.runner.cleanup()
