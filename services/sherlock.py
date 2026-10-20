import asyncio
import re
import sys
import time
from typing import Any

from aiogram.types import Message

from core.upupa_utils import normalize_upupa_command
from infrastructure.subprocess_runner import run_subprocess


# Sherlock опрашивает сотни сайтов несколько минут, поэтому одновременно
# идёт только один поиск, а каждый пользователь ждёт кулдаун между запусками.
SHERLOCK_USER_COOLDOWN_SECONDS = 600
_sherlock_semaphore = asyncio.Semaphore(1)
_last_search_started: dict[int, float] = {}


class SherlockSearch:
    """Асинхронный запуск Sherlock и парсинг найденных профилей."""

    TIMEOUT_SECONDS = 320
    COMMAND_PREFIX = "упупа ищи"

    async def search(self, username: str) -> dict[str, Any]:
        username = username.strip()
        if not username:
            return {
                "username": username,
                "found": [],
                "raw_output": "",
                "error": "Не указан username для поиска.",
            }

        # "--" не даёт значению вроде "--output=/path" разобраться как опция.
        result = await run_subprocess(
            [sys.executable, "-m", "sherlock_project", "--", username],
            timeout=self.TIMEOUT_SECONDS,
        )

        if result.timed_out:
            return {
                "username": username,
                "found": [],
                "raw_output": "",
                "error": f"❌ Превышено время ожидания ({self.TIMEOUT_SECONDS} сек)",
            }

        stdout_text = result.stdout_text
        stderr_text = result.stderr_text
        raw_output = "\n".join(part for part in [stdout_text, stderr_text] if part).strip()
        found_urls = self._parse_found_urls(stdout_text)

        error_text = None
        if result.returncode != 0:
            details = stderr_text.strip() or stdout_text.strip() or "Неизвестная ошибка Sherlock."
            error_text = f"Ошибка запуска Sherlock (code={result.returncode}):\n{details}"

        return {
            "username": username,
            "found": found_urls,
            "raw_output": raw_output,
            "error": error_text,
        }

    @staticmethod
    def _parse_found_urls(raw_stdout: str) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()

        for line in raw_stdout.splitlines():
            for match in re.findall(r"https?://[^\s\]\)>'\"\\]+", line):
                clean_url = match.rstrip(".,!?:;")
                if clean_url.startswith(("http://", "https://")) and clean_url not in seen:
                    seen.add(clean_url)
                    urls.append(clean_url)

        return urls


def is_sherlock_command(text: str | None) -> bool:
    if not text:
        return False
    return normalize_upupa_command(text).startswith(SherlockSearch.COMMAND_PREFIX)


async def process_sherlock_command(message: Message) -> None:
    normalized = normalize_upupa_command(message.text or "")
    username = normalized[len(SherlockSearch.COMMAND_PREFIX):].strip()

    if not username:
        await message.reply("Укажи username после команды: упупа ищи <username>")
        return

    requester_id = message.from_user.id if message.from_user else message.chat.id
    now = time.monotonic()
    for stale_id, started in list(_last_search_started.items()):
        if now - started >= SHERLOCK_USER_COOLDOWN_SECONDS:
            del _last_search_started[stale_id]
    last_started = _last_search_started.get(requester_id)
    if last_started is not None:
        wait_minutes = max(1, round((SHERLOCK_USER_COOLDOWN_SECONDS - (now - last_started)) / 60))
        await message.reply(f"⏳ Ты недавно уже искал. Попробуй снова через {wait_minutes} мин.")
        return

    if _sherlock_semaphore.locked():
        await message.reply("⏳ Я уже ищу чьи-то аккаунты. Подожди, пока закончу, и попробуй снова.")
        return

    async with _sherlock_semaphore:
        _last_search_started[requester_id] = now
        await message.reply(f"🔎 Ищу аккаунты этого пидораса: {username}")

        sherlock_search = SherlockSearch()
        result = await sherlock_search.search(username)

    if result["error"]:
        await message.reply(result["error"])
        return

    found = result["found"]
    if found:
        await message.reply("\n".join(found))
    else:
        await message.reply("Нихуя не нашел, мутный тип.")
