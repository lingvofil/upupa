import asyncio
import logging
from aiogram import types, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Хранилище состояний игр для каждого чата
# Ключ - ID чата, значение - словарь с состоянием игры
game_states = {}

async def start_egra(message: types.Message, bot: Bot):
    """Начинает новую игру в чате."""
    chat_id = message.chat.id
    
    if chat_id in game_states and game_states[chat_id].get("is_active", False):
        await message.reply("Мы уже и так играем, ебана!")
        return
        
    game_states[chat_id] = {
        "is_active": True,
        "options": ["1", "2", "3"],
        "poll_message_id": None,
        "poll_id": None,
        "final_button_message_id": None, # Для ID сообщения с последней кнопкой
    }
    
    await message.answer("Внеманее! Наченаем играть в прекольную егру!")
    await send_game_poll(chat_id, bot)


async def send_game_poll(chat_id: int, bot: Bot):
    """Отправляет или обновляет опрос для игры."""
    game = game_states.get(chat_id)
    if not game or not game["is_active"]:
        return

    try:
        poll_message = await bot.send_poll(
            chat_id=chat_id,
            question="НАЖМИТЕ КНОПАЧЬКУ))0",
            options=game["options"],
            is_anonymous=False,
            allows_multiple_answers=False,
        )
        game["poll_message_id"] = poll_message.message_id
        game["poll_id"] = poll_message.poll.id
    except Exception as e:
        logging.error(f"Не удалось отправить опрос в чат {chat_id}: {e}")
        if chat_id in game_states:
            del game_states[chat_id]


async def send_final_button(chat_id: int, bot: Bot):
    """Отправляет сообщение с инлайн-кнопкой для последнего выбора."""
    game = game_states.get(chat_id)
    if not game or not game["is_active"] or len(game["options"]) != 1:
        return

    last_option = game["options"][0]
    builder = InlineKeyboardBuilder()
    # callback_data поможет нам опознать нажатие именно этой кнопки
    builder.add(types.InlineKeyboardButton(
        text=last_option,
        callback_data=f"egra_final_choice"
    ))

    msg = await bot.send_message(
        chat_id,
        "А ЕТУ КНОПАЧЬКУ))0)",
        reply_markup=builder.as_markup()
    )
    game["final_button_message_id"] = msg.message_id


async def handle_egra_answer(poll_answer: types.PollAnswer, bot: Bot):
    """Обрабатывает ответ на опрос в игре."""
    game = None
    chat_id = None
    for cid, g in game_states.items():
        if g.get("poll_id") == poll_answer.poll_id:
            game = g
            chat_id = cid
            break
            
    if not game:
        return False

    user = poll_answer.user
    chosen_option_index = poll_answer.option_ids[0]
    chosen_option_text = game["options"][chosen_option_index]

    try:
        await bot.delete_message(chat_id, game["poll_message_id"])
    except TelegramBadRequest as e:
        logging.warning(f"Не удалось удалить сообщение с опросом {game['poll_message_id']} в чате {chat_id}: {e}")

    game["options"].pop(chosen_option_index)

    await bot.send_message(chat_id, f"{user.full_name} ножало \"{chosen_option_text}\".., прадолжаем..))00")

    # ИСПРАВЛЕННАЯ ЛОГИКА
    if len(game["options"]) > 1:
        # Если осталось больше одной кнопки, создаем новый опрос
        await send_game_poll(chat_id, bot)
    elif len(game["options"]) == 1:
        # Если осталась одна кнопка, переключаемся на инлайн-кнопку
        await send_final_button(chat_id, bot)
        
    return True


async def handle_final_button_press(callback_query: types.CallbackQuery, bot: Bot):
    """Обрабатывает нажатие последней инлайн-кнопки."""
    chat_id = callback_query.message.chat.id
    game = game_states.get(chat_id)

    if not game or not game["is_active"]:
        await callback_query.answer("Игра уже закончилась, поздняк метаться.")
        return

    user = callback_query.from_user
    
    await bot.send_message(chat_id, f"УРА! У НАС ПОБЕДИТЕЛЬ! ИГРА ОКОНЧЕНА! ПОЗДРАВЛЯЕМ, ТЫ КОНЧЕНЫ ХУЕСОС, {user.full_name}!")
    
    # Удаляем сообщение с кнопкой
    try:
        if game.get("final_button_message_id"):
            await bot.delete_message(chat_id, game["final_button_message_id"])
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение с финальной кнопкой: {e}")

    # Очищаем состояние игры
    if chat_id in game_states:
        del game_states[chat_id]
    
    await callback_query.answer()
