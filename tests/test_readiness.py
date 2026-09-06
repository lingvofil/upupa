import asyncio
import json
import os
import sqlite3
import time
from types import SimpleNamespace

from aiohttp import ClientSession
from aiogram.methods import GetMe, GetUpdates
import pytest

from app.lifecycle import TaskSupervisor
from app.readiness import PollingHealth, ReadinessServer
from infrastructure.persistence.health import check_databases


def test_polling_requires_a_successful_get_updates_and_recovers_from_failure():
    async def scenario():
        state = PollingHealth()

        async def ok(bot, method):
            return []

        async def fail(bot, method):
            raise RuntimeError("offline")

        assert not state.ready()
        await state.observe(ok, None, GetMe())
        assert not state.ready()  # token validity alone is not readiness
        assert await state.observe(ok, None, GetUpdates()) == []
        assert state.ready()
        with pytest.raises(RuntimeError):
            await state.observe(fail, None, GetUpdates())
        assert not state.ready()
        await state.observe(ok, None, GetUpdates())
        assert state.ready()
        state.last_success -= 91
        assert not state.ready()
        await state.observe(ok, None, GetUpdates())
        state.stopping = True
        assert not state.ready()

    asyncio.run(scenario())


def test_http_endpoint_reports_actual_process_and_missing_task():
    async def scenario():
        state = PollingHealth(last_success=time.monotonic())
        supervisor = TaskSupervisor()
        server = ReadinessServer(state, supervisor, lambda: None, {"worker"}, port=0)
        task = supervisor.start(asyncio.Event().wait(), name="worker")
        await server.start()
        port = server.runner.addresses[0][1]
        try:
            async with ClientSession() as client:
                async with client.get(f"http://127.0.0.1:{port}/ready") as response:
                    assert response.status == 200
                    assert (await response.json())["pid"] == os.getpid()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                async with client.get(f"http://127.0.0.1:{port}/ready") as response:
                    assert response.status == 503
                    assert (await response.json())["missing_tasks"] == ["worker"]
        finally:
            await server.stop()
            await supervisor.stop()
        assert not state.ready()

    asyncio.run(scenario())


def test_database_errors_make_readiness_unhealthy_without_exposing_details():
    def fail():
        raise OSError("private/path/or/secret")

    server = ReadinessServer(PollingHealth(last_success=time.monotonic()),
                             SimpleNamespace(task_names=()), fail, ())
    response = asyncio.run(server.handle(None))
    assert response.status == 503
    assert not json.loads(response.text)["checks"]["databases"]
    assert "private" not in response.text


def test_probe_requires_existing_databases_and_tables(tmp_path):
    stats, world = tmp_path / "stats.db", tmp_path / "world.db"
    with pytest.raises(sqlite3.OperationalError):
        check_databases(stats, world)
    assert not stats.exists()
    for path, tables in ((stats, ("message_stats", "rank_counters")),
                         (world, ("world_states",))):
        conn = sqlite3.connect(path)
        try:
            for table in tables:
                conn.execute(f"CREATE TABLE {table}(id INTEGER)")
            conn.commit()
        finally:
            conn.close()
    check_databases(stats, world)
    history = tmp_path / "history.db"
    with pytest.raises(sqlite3.OperationalError):
        check_databases(stats, world, history)
    assert not history.exists()
    conn = sqlite3.connect(stats)
    try:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError):
            check_databases(stats, world)
    finally:
        conn.close()


def test_application_closes_resources_when_state_initialization_fails(monkeypatch):
    from tests import test_smoke_imports
    del test_smoke_imports
    from app.bootstrap import UpupaApplication
    from unittest.mock import AsyncMock, Mock

    bot = SimpleNamespace(session=SimpleNamespace(middleware=Mock(), close=AsyncMock()))
    readiness = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    supervisor = SimpleNamespace(stop=AsyncMock())
    app = UpupaApplication(bot=bot, dispatcher=object(), supervisor=supervisor)
    monkeypatch.setattr(app, "create_readiness_server", lambda: readiness)
    monkeypatch.setattr(app, "initialize_state", Mock(side_effect=ValueError("bad migration")))
    with pytest.raises(ValueError):
        asyncio.run(app.run())
    readiness.start.assert_not_awaited()
    readiness.stop.assert_awaited_once()
    supervisor.stop.assert_awaited_once()
    bot.session.close.assert_awaited_once()
