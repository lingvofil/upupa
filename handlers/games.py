"""Хэндлеры: Егра, мемы, кракадил.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

from aiogram import Bot, F, types
from aiogram.types import Message, PollAnswer
from config import (
    bot
)
from games.egra import start_egra, handle_egra_answer, handle_final_button_press
from services import memegenerator
from games import crocodile, reverse_crocodile
from AI.quiz import process_poll_answer


router = Router(name="games")


@router.message(F.text.lower() == "егра")
async def egra_command_handler(message: types.Message):
    await start_egra(message, bot)

@router.poll_answer()
async def handle_poll_answers(poll_answer: PollAnswer, bot: Bot):
    is_egra_handled = await handle_egra_answer(poll_answer, bot)
    if not is_egra_handled:
        await process_poll_answer(poll_answer, bot)

@router.callback_query(F.data == "egra_final_choice")
async def egra_callback_handler(callback_query: types.CallbackQuery):
    await handle_final_button_press(callback_query, bot)

@router.message(F.text.lower().in_(["мем", "meme"]))
async def meme_command_handler(message: Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action="upload_photo")
    reply_text = message.reply_to_message.text if message.reply_to_message else None
    photo = await memegenerator.create_meme_image(message.chat.id, reply_text)
    if photo:
        await message.answer_photo(photo)
    else:
        await message.answer("Ошибка при создании мема.")

@router.message(F.text.lower() == "кракадил")
async def start_croc(message: types.Message):
    print("CROC BOT ID:", id(bot))
    await crocodile.handle_start_game(message)

@router.callback_query(F.data.startswith("cr_"))
async def croc_callback(callback: types.CallbackQuery):
    if callback.data == "cr_restart":
        await crocodile.handle_start_game(callback.message)
        await callback.answer()
    else:
        await crocodile.handle_callback(callback)

@router.message(lambda m: m.text and m.text.lower().strip() == "кракадил стоп")
async def stop_croc_text(message: types.Message):
    await crocodile.handle_text_stop(message)

@router.message(F.text.lower() == "кракадил наоборот")
async def start_reverse_croc(message: types.Message):
    await reverse_crocodile.start_game(message)

@router.callback_query(F.data.startswith("rcroc_"))
async def reverse_croc_callback(callback: types.CallbackQuery):
    await reverse_crocodile.handle_callback(callback)
