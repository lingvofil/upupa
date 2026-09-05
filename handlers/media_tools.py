"""Хэндлеры: ПУП (YTP), ускорение/замедление медиа.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

import os

from aiogram import types
from core.loader import bot
from core.settings import BLOCKED_USERS
from services.ytp import handle_ytp_command
from services.media_change import handle_fast_command, handle_reverse_command, handle_slow_command

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".gif", ".ogg"}


def is_video_document(msg: types.Message) -> bool:
    if not msg or not msg.document:
        return False
    if msg.document.mime_type and msg.document.mime_type.startswith("video/"):
        return True
    if msg.document.file_name:
        ext = os.path.splitext(msg.document.file_name)[1].lower()
        return ext in SUPPORTED_EXTENSIONS
    return False


def is_ogg_document(msg: types.Message) -> bool:
    if not msg or not msg.document:
        return False
    if msg.document.mime_type == "audio/ogg":
        return True
    if msg.document.file_name:
        ext = os.path.splitext(msg.document.file_name)[1].lower()
        return ext == ".ogg"
    return False


def is_media_speed_command(message: types.Message, command: str) -> bool:
    return bool(
        (message.text and message.text.lower().strip() == command)
        or (message.caption and message.caption.lower().strip() == command)
    )


def is_reverse_command(message: types.Message) -> bool:
    return bool(
        (message.text and message.text.lower().strip() == "наоборот")
        or (message.caption and message.caption.lower().strip() == "наоборот")
    )


router = Router(name="media_tools")


@router.message(
    lambda message: (
        (
            message.text and
            message.text.lower().strip() == "пуп" and
            message.reply_to_message and
            (
                message.reply_to_message.video
                or getattr(message.reply_to_message, "video_note", None)
                or message.reply_to_message.animation
                or is_video_document(message.reply_to_message)
                or message.reply_to_message.audio
                or message.reply_to_message.voice
                or is_ogg_document(message.reply_to_message)
                or message.reply_to_message.sticker
            )
        )
        or
        (
            (
                message.video
                or getattr(message, "video_note", None)
                or message.animation
                or is_video_document(message)
                or message.audio
                or message.voice
                or is_ogg_document(message)
                or message.sticker
            ) and
            message.caption and
            message.caption.lower().strip() == "пуп"
        )
    ) and message.from_user.id not in BLOCKED_USERS
)
async def handle_pup_command(message: types.Message):
    await handle_ytp_command(message, bot)


@router.message(
    lambda message: is_media_speed_command(message, "быстрее")
    and message.from_user.id not in BLOCKED_USERS
)
async def handle_faster_media_command(message: types.Message):
    await handle_fast_command(message, bot)


@router.message(
    lambda message: is_media_speed_command(message, "медленнее")
    and message.from_user.id not in BLOCKED_USERS
)
async def handle_slower_media_command(message: types.Message):
    await handle_slow_command(message, bot)


@router.message(
    lambda message: is_reverse_command(message)
    and message.from_user.id not in BLOCKED_USERS
)
async def handle_reverse_media_command(message: types.Message):
    await handle_reverse_command(message, bot)

# ================== БЛОК 6: ХЭНДЛЕРЫ С AI ==================

# ================== БЛОК 6.1: ПЕРЕКЛЮЧЕНИЕ МОДЕЛЕЙ ==================
