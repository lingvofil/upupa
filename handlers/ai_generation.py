"""Хэндлеры: Генерация и редактирование картинок.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

from aiogram import types
from config import (
    BLOCKED_USERS
)
from AI.adddescribe import (
    handle_add_text_command
)
from AI.picgeneration import (
    handle_pun_image_command,
    handle_image_generation_command,
    handle_redraw_command,
    handle_mugshot_command,
    handle_edit_command,
    handle_kandinsky_generation_command,
    handle_nvidia_command
)

router = Router(name="ai_generation")


@router.message(
    lambda message: (
        (
            (message.photo and message.caption and "отредактируй" in message.caption.lower()) or
            (message.document and message.caption and "отредактируй" in message.caption.lower()) or
            (message.text and "отредактируй" in message.text.lower() and message.reply_to_message and 
            (message.reply_to_message.photo or message.reply_to_message.document))
        ) and message.from_user.id not in BLOCKED_USERS
    )
)
async def edit_image(message: types.Message):
    await handle_edit_command(message)

@router.message(
    lambda message: (
        message.from_user.id not in BLOCKED_USERS and
        message.text and (
            message.text.lower().startswith("нарисуй") or
            (message.text.lower().strip() == "нарисуй" and message.reply_to_message)
        )
    )
)
async def generate_image(message: types.Message):
    await handle_image_generation_command(message)
    
@router.message(
    lambda message: (
        message.from_user.id not in BLOCKED_USERS and
        message.text and (
            message.text.lower().startswith("сгенерируй") or
            (message.text.lower().strip() == "сгенерируй" and message.reply_to_message)
        )
    )
)
async def generate_image_kandinsky(message: types.Message):
    await handle_kandinsky_generation_command(message)

@router.message(
    lambda message: (
        (
            (message.photo and message.caption and "перерисуй" in message.caption.lower()) or
            (message.document and message.caption and "перерисуй" in message.caption.lower()) or
            (message.text and "перерисуй" in message.text.lower() and message.reply_to_message and 
            (message.reply_to_message.photo or message.reply_to_message.document))
        ) and message.from_user.id not in BLOCKED_USERS
    )
)
async def redraw_image(message: types.Message):
    await handle_redraw_command(message)

@router.message(
    lambda message: (
        (
            (message.photo and message.caption and "магшот" in message.caption.lower()) or
            (message.document and message.caption and "магшот" in message.caption.lower()) or
            (message.sticker and message.caption and "магшот" in message.caption.lower()) or
            (
                message.text and "магшот" in message.text.lower() and message.reply_to_message and
                (
                    message.reply_to_message.photo or
                    (message.reply_to_message.document and message.reply_to_message.document.mime_type and message.reply_to_message.document.mime_type.startswith("image/")) or
                    (
                        message.reply_to_message.sticker and
                        not message.reply_to_message.sticker.is_animated and
                        not message.reply_to_message.sticker.is_video
                    )
                )
            )
        ) and message.from_user.id not in BLOCKED_USERS
    )
)
async def mugshot_image(message: types.Message):
    await handle_mugshot_command(message)

@router.message(
    lambda message: (
        (
            (message.photo and message.caption and "нвидиа" in message.caption.lower()) or
            (message.document and message.caption and "нвидиа" in message.caption.lower()) or
            (message.text and "нвидиа" in message.text.lower() and message.reply_to_message and 
            (
                message.reply_to_message.photo or 
                message.reply_to_message.document or
                (message.reply_to_message.sticker and 
                 not message.reply_to_message.sticker.is_animated and 
                 not message.reply_to_message.sticker.is_video)
            ))
        ) and message.from_user.id not in BLOCKED_USERS
    )
)
async def nvidia_image(message: types.Message):
    await handle_nvidia_command(message)

@router.message(
    lambda message: message.text and 
    message.text.lower().strip() == "скаламбурь" and 
    message.from_user.id not in BLOCKED_USERS
)
async def generate_pun_with_image(message: types.Message):
    await handle_pun_image_command(message)
    
@router.message(
    lambda message: (
        (message.photo and message.caption and "добавь" in message.caption.lower()) or 
        (message.text and "добавь" in message.text.lower() and message.reply_to_message and (message.reply_to_message.photo or message.reply_to_message.document))
    ) and message.from_user.id not in BLOCKED_USERS
)
async def add_text_to_image(message: types.Message):
    await handle_add_text_command(message)
