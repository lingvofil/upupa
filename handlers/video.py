"""Хэндлеры: генерация видео (упупа сними, оживи).

Этап 9. Роутер подключается ПЕРЕД dialog (catch-all) — см. handlers/__init__.py.
"""
from aiogram import Router, types

from core.inflight import single_flight
from core.loader import bot
import AI.videogeneration as videogeneration
from AI.media_execution import install_into_videogeneration

install_into_videogeneration(videogeneration)

from AI.videogeneration import process_video_generation, process_animate_photo

router = Router(name="video")

VIDEO_BUSY_TEXT = "В этом чате уже снимается видео, дождитесь его."


@router.message(
    lambda message: message.text and (
        message.text.lower().startswith("упупа сними")
        or message.text.lower().startswith("упупа, сними")
    )
)
@single_flight("video", per="chat", busy_text=VIDEO_BUSY_TEXT)
async def generate_video_command(message: types.Message):
    await process_video_generation(message, bot)


@router.message(
    lambda message: (
        message.text
        and message.text.lower().startswith("оживи")
        and message.reply_to_message is not None
    ) or (
        message.photo
        and message.caption
        and message.caption.lower().startswith("оживи")
    )
)
@single_flight("video", per="chat", busy_text=VIDEO_BUSY_TEXT)
async def animate_photo_command(message: types.Message):
    await process_animate_photo(message, bot)
