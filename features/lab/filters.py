"""aiogram-фильтр: эксперимент включён в чате этого update.

    @router.message(ExperimentEnabled("oracle"), F.text.lower() == "оракул")

Эксперимент с ``public=True`` проходит в любом чате. Если эксперимент выключен, фильтр возвращает ``False`` и update идёт дальше
по роутерам, как будто эксперимента нет.
"""

from __future__ import annotations

from typing import Any

from aiogram.filters import Filter

from features.lab.flags import get_lab_flags
from features.lab.registry import get_experiment


def update_chat_id(event: Any) -> int | None:
    """chat_id для Message, CallbackQuery и прочих update с чатом."""
    chat = getattr(event, "chat", None)
    if chat is None:
        message = getattr(event, "message", None)
        chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    return chat_id if isinstance(chat_id, int) else None


class ExperimentEnabled(Filter):
    def __init__(self, key: str) -> None:
        # Опечатка в ключе должна падать на импорте, а не молча выключать игру.
        experiment = get_experiment(key)
        self.key = experiment.key
        self.public = experiment.public

    async def __call__(self, event: Any) -> bool:
        chat_id = update_chat_id(event)
        if chat_id is None:
            return False
        if self.public:
            return True
        return get_lab_flags().is_enabled(chat_id, self.key)
