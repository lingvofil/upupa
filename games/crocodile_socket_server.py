"""Lifecycle wrapper for the Crocodile Socket.IO aiohttp server."""

import asyncio
import logging

from aiohttp import web

from games import crocodile


async def crocodile_socket_server_loop() -> None:
    """Serve Socket.IO until application shutdown, then release the listener."""
    runner = web.AppRunner(crocodile.app)
    await runner.setup()
    try:
        site = web.TCPSite(
            runner,
            crocodile.SOCKET_SERVER_HOST,
            crocodile.SOCKET_SERVER_PORT,
        )
        await site.start()
        logging.info(
            "[crocodile] Socket.io server running on %s:%s",
            crocodile.SOCKET_SERVER_HOST,
            crocodile.SOCKET_SERVER_PORT,
        )
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


__all__ = ["crocodile_socket_server_loop"]
