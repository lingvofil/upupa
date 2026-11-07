"""Админская команда «лаба»: включение экспериментов игровой лаборатории по чатам."""

import logging

from aiogram import F, Router, types

from core.settings import ADMIN_ID
from features.lab.commands import apply_lab_command, parse_lab_command
from features.lab.events import log_event
from features.lab.flags import get_lab_flags

router = Router(name="lab")


def _is_lab_command(message: types.Message) -> bool:
    return parse_lab_command(message.text) is not None


# Фильтр по админу стоит в декораторе: чужое «лаба» уходит дальше по роутерам.
@router.message(F.from_user.id == ADMIN_ID, _is_lab_command)
async def handle_lab_command(message: types.Message) -> None:
    command = parse_lab_command(message.text)
    flags = get_lab_flags()
    result = apply_lab_command(flags, message.chat.id, command)

    if result.changed:
        try:
            await flags.persist()
        except Exception:
            logging.exception("[lab] не удалось сохранить флаги экспериментов")
            await message.reply(result.text + "\n\nНо на диск не сохранилось: после рестарта откатится.")
            return
        for key, enabled in result.toggled:
            await log_event(
                key,
                "lab_enabled" if enabled else "lab_disabled",
                chat_id=message.chat.id,
                user_id=message.from_user.id,
            )

    await message.reply(result.text)
