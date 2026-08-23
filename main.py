"""Точка входа Упупы.

Сборка приложения и управление жизненным циклом находятся в app.bootstrap.
"""

import asyncio

from app.bootstrap import run_application


async def main():
    await run_application()


if __name__ == "__main__":
    asyncio.run(main())
