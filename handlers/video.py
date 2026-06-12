"""Хэндлеры: генерация видео (упупа сними, оживи).

Этап 9. Роутер подключается ПЕРЕД dialog (catch-all) — см. handlers/__init__.py.
"""
from aiogram import Router, types

from core.loader import bot
from AI.videogeneration import process_video_generation, process_animate_photo

router = Router(name="video")


@router.message(
    lambda message: message.text and (
        message.text.lower().startswith("упупа сними")
        or message.text.lower().startswith("упупа, сними")
    )
)
async def generate_video_command(message: types.Message):
    await process_video_generation(message, bot)


@router.message(
    lambda message: message.text
    and message.text.lower().startswith("оживи")
    and message.reply_to_message is not None
)
async def animate_photo_command(message: types.Message):
    await process_animate_photo(message, bot)
