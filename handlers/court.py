"""Telegram transport for local Upupa court and judicial practice."""

from __future__ import annotations

from aiogram import Router, types

from core.settings import BLOCKED_USERS
from core.upupa_utils import normalize_upupa_command
from features.court import adjudicate_chat_case, format_judicial_practice, list_chat_cases


router = Router(name="court")


def _normalized(message: types.Message) -> str:
    return normalize_upupa_command(message.text or "")


@router.message(
    lambda message: bool(message.text)
    and message.from_user
    and message.from_user.id not in BLOCKED_USERS
    and (_normalized(message) == "упупа суд" or _normalized(message).startswith("упупа суд "))
)
async def upupa_court(message: types.Message):
    status = await message.reply("⚖️ Суд идёт. Секретарь уже потерял половину материалов дела...")
    try:
        case = await adjudicate_chat_case(message)
    except Exception:
        await status.edit_text("⚖️ Суд удалился в совещательную комнату и там сломался.")
        return
    if case is None:
        await status.edit_text("⚖️ Тут нечего судить. Реплайни на спор или дождись хотя бы нормальной перепалки.")
        return
    await status.edit_text(f"⚖️ Дело №{case['case_id']}\n\n{case['verdict']}")


@router.message(
    lambda message: bool(message.text)
    and message.from_user
    and message.from_user.id not in BLOCKED_USERS
    and _normalized(message) in {"судебная практика", "упупа судебная практика"}
)
async def judicial_practice(message: types.Message):
    cases = await list_chat_cases(message.chat.id)
    await message.reply(format_judicial_practice(cases))
