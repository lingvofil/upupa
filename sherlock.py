import asyncio
import re
import sys
from typing import Any

from aiogram.types import Message

from upupa_utils import normalize_upupa_command


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

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "sherlock_project",
            username,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return {
                "username": username,
                "found": [],
                "raw_output": "",
                "error": f"❌ Превышено время ожидания ({self.TIMEOUT_SECONDS} сек)",
            }

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        raw_output = "\n".join(part for part in [stdout_text, stderr_text] if part).strip()
        found_urls = self._parse_found_urls(stdout_text)

        error_text = None
        if process.returncode != 0:
            details = stderr_text.strip() or stdout_text.strip() or "Неизвестная ошибка Sherlock."
            error_text = f"Ошибка запуска Sherlock (code={process.returncode}):\n{details}"

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
