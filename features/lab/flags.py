"""Флаги экспериментов по чатам.

Формат файла: ``{"<chat_id>": ["oracle", ...]}``. По умолчанию всё выключено:
эксперимент работает только в чатах, где его явно включили командой «лаба».
Файл читается на startup (``load_lab_flags``), а не при импорте.
"""

from __future__ import annotations

import asyncio
import logging

from core.json_repository import JsonFileRepository, JsonRepository
from core.paths import LAB_FLAGS_PATH

logger = logging.getLogger(__name__)


class ExperimentFlags:
    def __init__(self, repository: JsonRepository) -> None:
        self._repository = repository
        self._enabled: dict[str, set[str]] = {}
        self._save_lock: asyncio.Lock | None = None

    def load(self) -> None:
        """Прочитать флаги; битый или отсутствующий файл = всё выключено."""
        try:
            data = self._repository.load()
        except FileNotFoundError:
            self._enabled = {}
            return
        except Exception as exc:
            logger.error("[lab] не удалось прочитать флаги экспериментов: %s", exc)
            self._enabled = {}
            return

        if not isinstance(data, dict):
            logger.warning("[lab] файл флагов повреждён, все эксперименты выключены")
            self._enabled = {}
            return

        self._enabled = {
            str(chat_id): {str(key) for key in keys}
            for chat_id, keys in data.items()
            if isinstance(keys, list) and keys
        }

    def is_enabled(self, chat_id: int | str, key: str) -> bool:
        return key in self._enabled.get(str(chat_id), ())

    def enabled_in(self, chat_id: int | str) -> list[str]:
        return sorted(self._enabled.get(str(chat_id), ()))

    def enable(self, chat_id: int | str, key: str) -> bool:
        """Включить; ``True``, если состояние изменилось."""
        keys = self._enabled.setdefault(str(chat_id), set())
        if key in keys:
            return False
        keys.add(key)
        return True

    def disable(self, chat_id: int | str, key: str) -> bool:
        """Выключить; ``True``, если состояние изменилось."""
        chat = str(chat_id)
        keys = self._enabled.get(chat)
        if not keys or key not in keys:
            return False
        keys.discard(key)
        if not keys:
            del self._enabled[chat]
        return True

    def disable_all(self, chat_id: int | str) -> list[str]:
        """Выключить всё в чате; вернуть то, что было включено."""
        return sorted(self._enabled.pop(str(chat_id), set()))

    def snapshot(self) -> dict[str, list[str]]:
        return {chat: sorted(keys) for chat, keys in sorted(self._enabled.items()) if keys}

    async def persist(self) -> None:
        """Сохранить на диск вне event loop; записи идут строго по очереди."""
        if self._save_lock is None:
            self._save_lock = asyncio.Lock()
        async with self._save_lock:
            # Снимок берём под замком: последняя запись всегда несёт свежее состояние.
            snapshot = self.snapshot()
            await asyncio.to_thread(self._repository.save, snapshot)


_flags = ExperimentFlags(JsonFileRepository(LAB_FLAGS_PATH))


def get_lab_flags() -> ExperimentFlags:
    return _flags


def configure_lab_flags(flags: ExperimentFlags) -> None:
    """Подменить хранилище флагов (тесты, альтернативный backend)."""
    global _flags
    _flags = flags


def load_lab_flags() -> None:
    """Явная загрузка на startup приложения."""
    _flags.load()


def is_experiment_enabled(chat_id: int | str, key: str) -> bool:
    return _flags.is_enabled(chat_id, key)
