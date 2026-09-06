"""Точка входа Упупы.

Сборка приложения и управление жизненным циклом находятся в app.bootstrap.
"""

import asyncio

from app.bootstrap import run_application
from core.time_utils import configure_process_timezone


async def main():
    # The bot historically schedules user-facing activity in Europe/Moscow.
    # Configure local wall-clock semantics explicitly instead of inheriting
    # whatever timezone happens to be set on the VPS.
    configure_process_timezone()
    await run_application()


if __name__ == "__main__":
    asyncio.run(main())
