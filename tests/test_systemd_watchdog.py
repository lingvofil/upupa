import asyncio
import os
import socket

import pytest

from app import systemd_watchdog
from app.systemd_watchdog import systemd_watchdog_loop, watchdog_interval_seconds


def test_interval_is_half_of_systemd_timeout():
    environ = {"NOTIFY_SOCKET": "/run/systemd/notify", "WATCHDOG_USEC": "180000000"}

    assert watchdog_interval_seconds(environ) == 90.0


def test_interval_for_this_process_when_watchdog_pid_matches():
    environ = {
        "NOTIFY_SOCKET": "/run/systemd/notify",
        "WATCHDOG_USEC": "180000000",
        "WATCHDOG_PID": str(os.getpid()),
    }

    assert watchdog_interval_seconds(environ) == 90.0


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"NOTIFY_SOCKET": "/run/systemd/notify"},
        {"WATCHDOG_USEC": "180000000"},
        {"NOTIFY_SOCKET": "/run/systemd/notify", "WATCHDOG_USEC": "0"},
        {"NOTIFY_SOCKET": "/run/systemd/notify", "WATCHDOG_USEC": "soon"},
        {
            "NOTIFY_SOCKET": "/run/systemd/notify",
            "WATCHDOG_USEC": "180000000",
            "WATCHDOG_PID": "1",
        },
    ],
)
def test_watchdog_is_disabled_without_systemd_request(environ):
    assert watchdog_interval_seconds(environ) is None


def test_loop_keeps_pinging_after_send_failure(monkeypatch):
    pings = []

    def flaky_ping(notify_socket):
        pings.append(notify_socket)
        if len(pings) == 1:
            raise OSError("systemd is busy")

    monkeypatch.setattr(systemd_watchdog, "_send_watchdog_ping", flaky_ping)

    async def scenario():
        task = asyncio.create_task(
            systemd_watchdog_loop(0.001, environ={"NOTIFY_SOCKET": "/run/notify"})
        )
        while len(pings) < 3:
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert pings[:3] == ["/run/notify"] * 3


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="нужен unix-сокет")
def test_ping_reaches_notify_socket(tmp_path):
    path = str(tmp_path / "notify")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(path)
        systemd_watchdog._send_watchdog_ping(path)

        assert receiver.recv(64) == b"WATCHDOG=1"
