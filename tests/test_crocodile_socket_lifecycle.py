import asyncio

import pytest

from games import crocodile


def test_socket_server_stays_alive_until_cancelled_and_cleans_up(monkeypatch):
    events = []

    class FakeRunner:
        def __init__(self, app):
            assert app is crocodile.app

        async def setup(self):
            events.append("setup")

        async def cleanup(self):
            events.append("cleanup")

    class FakeSite:
        def __init__(self, runner, host, port):
            assert isinstance(runner, FakeRunner)
            assert host == crocodile.SOCKET_SERVER_HOST
            assert port == crocodile.SOCKET_SERVER_PORT

        async def start(self):
            events.append("start")

    monkeypatch.setattr(crocodile.web, "AppRunner", FakeRunner)
    monkeypatch.setattr(crocodile.web, "TCPSite", FakeSite)

    async def scenario():
        task = asyncio.create_task(crocodile.start_socket_server())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert events == ["setup", "start", "cleanup"]


def test_socket_server_cleans_up_when_bind_fails(monkeypatch):
    events = []

    class FakeRunner:
        def __init__(self, app):
            assert app is crocodile.app

        async def setup(self):
            events.append("setup")

        async def cleanup(self):
            events.append("cleanup")

    class BrokenSite:
        def __init__(self, runner, host, port):
            assert isinstance(runner, FakeRunner)

        async def start(self):
            events.append("start")
            raise OSError("address already in use")

    monkeypatch.setattr(crocodile.web, "AppRunner", FakeRunner)
    monkeypatch.setattr(crocodile.web, "TCPSite", BrokenSite)

    with pytest.raises(OSError, match="address already in use"):
        asyncio.run(crocodile.start_socket_server())

    assert events == ["setup", "start", "cleanup"]
