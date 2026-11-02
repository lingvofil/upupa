"""Сигнал живости для systemd-watchdog.

Бот может зависнуть, не падая: при нехватке памяти процесс жив, но event loop
стоит, и Restart=always не срабатывает. Сигнал уходит из самого event loop,
поэтому его пропажа означает, что бот действительно ничего не делает.
"""

import asyncio
from collections.abc import Mapping
import logging
import os
import socket


def watchdog_interval_seconds(environ: Mapping[str, str] = os.environ) -> float | None:
    """Вернуть период сигнала; None — если systemd не ждёт его от этого процесса."""
    if not environ.get("NOTIFY_SOCKET"):
        return None

    watchdog_pid = environ.get("WATCHDOG_PID")
    if watchdog_pid and watchdog_pid != str(os.getpid()):
        return None

    try:
        timeout_usec = int(environ.get("WATCHDOG_USEC", "0"))
    except ValueError:
        return None
    if timeout_usec <= 0:
        return None

    # Половина таймаута, как советует sd_watchdog_enabled(3).
    return timeout_usec / 2_000_000


def _send_watchdog_ping(notify_socket: str) -> None:
    address = f"\0{notify_socket[1:]}" if notify_socket.startswith("@") else notify_socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        sock.sendto(b"WATCHDOG=1", address)


async def systemd_watchdog_loop(
    interval_seconds: float,
    *,
    environ: Mapping[str, str] = os.environ,
    logger: logging.Logger | None = None,
) -> None:
    resolved_logger = logger or logging.getLogger(__name__)
    notify_socket = environ["NOTIFY_SOCKET"]

    while True:
        try:
            _send_watchdog_ping(notify_socket)
        except OSError:
            resolved_logger.exception("Failed to send systemd watchdog ping")
        await asyncio.sleep(interval_seconds)
