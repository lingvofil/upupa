import logging
from aiogram import types, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Хранилище состояний игр для каждого чата
# Ключ - ID чата, значение - словарь с состоянием игры
game_states = {}

async def start_egra(message: types.Message, bot: Bot):
    """Начинает или перезапускает игру в чате."""
    chat_id = message.chat.id
    
    # Старая проверка полностью удалена. 
    # Команда "егра" теперь всегда начинает новую игру, перезаписывая любое "зависшее" состояние.
    
    game = {
        "is_active": True,
        "options": ["1", "2", "3"],
        "poll_message_id": None,
        "poll_id": None,
        "final_button_message_id": None,
    }
    
    game_states[chat_id] = game
    await message.answer("Внеманее! Наченаем играть в прекольную егру!")
    if game_states.get(chat_id) is game:
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
        if game_states.get(chat_id) is not game:
            await _delete_stale_message(bot, chat_id, poll_message.message_id)
            return
        game["poll_message_id"] = poll_message.message_id
        game["poll_id"] = poll_message.poll.id
    except Exception as e:
        logging.error(f"Не удалось отправить опрос в чат {chat_id}: {e}")
        if game_states.get(chat_id) is game:
            del game_states[chat_id]


async def send_final_button(chat_id: int, bot: Bot):
    """Отправляет сообщение с инлайн-кнопкой для последнего выбора."""
    game = game_states.get(chat_id)
    if not game or not game["is_active"] or len(game["options"]) != 1:
        return

    last_option = game["options"][0]
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text=last_option,
        callback_data="egra_final_choice"
    ))

    msg = await bot.send_message(
        chat_id,
        "А ЕТУ КНОПАЧЬКУ))0)",
        reply_markup=builder.as_markup()
    )
    if game_states.get(chat_id) is not game:
        await _delete_stale_message(bot, chat_id, msg.message_id)
        return
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
            
    if not game or not game.get("is_active", False):
        return False

    user = poll_answer.user
    if user is None or not poll_answer.option_ids:
        return True
    chosen_option_index = poll_answer.option_ids[0]

    if not 0 <= chosen_option_index < len(game["options"]):
        logging.warning(f"Получен неверный индекс опции {chosen_option_index} в игре для чата {chat_id}.")
        return False

    chosen_option_text = game["options"][chosen_option_index]
    # Claim the poll before yielding; later answers belong to a closed round.
    game["poll_id"] = None

    try:
        if game.get("poll_message_id"):
            await bot.delete_message(chat_id, game["poll_message_id"])
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение с опросом {game.get('poll_message_id')} в чате {chat_id}: {e}")

    if game_states.get(chat_id) is not game:
        return True
    game["options"].pop(chosen_option_index)

    try:
        await bot.send_message(chat_id, f"{user.full_name} ножало \"{chosen_option_text}\".., прадолжаем..))00")
    except Exception:
        logging.exception("Failed to announce egra choice chat_id=%s", chat_id)
    if game_states.get(chat_id) is not game:
        return True

    if len(game["options"]) > 1:
        await send_game_poll(chat_id, bot)
    elif len(game["options"]) == 1:
        await send_final_button(chat_id, bot)
    else:
        await bot.send_message(chat_id, "Ой, чота сломалось, все кнопки кончились. Начните заново.")
        if game_states.get(chat_id) is game:
            del game_states[chat_id]
        
    return True


async def handle_final_button_press(callback_query: types.CallbackQuery, bot: Bot):
    """Обрабатывает нажатие последней инлайн-кнопки."""
    chat_id = callback_query.message.chat.id
    game = game_states.get(chat_id)

    if not game or not game.get("is_active") or callback_query.message.message_id != game.get("final_button_message_id"):
        await callback_query.answer("Игра уже закончилась или перезапущена, поздняк метаться.")
        if game is None or callback_query.message.message_id != game.get("final_button_message_id"):
            try:
                await callback_query.message.delete()
            except TelegramBadRequest:
                pass
        return

    user = callback_query.from_user
    
    game["is_active"] = False
    try:
        await bot.send_message(chat_id, f"УРА! У НАС ПОБЕДИТЕЛЬ! ИГРА ОКОНЧЕНА! ПОЗДРАВЛЯЕМ, ТЫ КОНЧЕНЫ ХУЕСОС, {user.full_name}!")
    except BaseException:
        if game_states.get(chat_id) is game:
            game["is_active"] = True
        raise
    
    try:
        if game.get("final_button_message_id"):
            await bot.delete_message(chat_id, game["final_button_message_id"])
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение с финальной кнопкой: {e}")

    if game_states.get(chat_id) is game:
        del game_states[chat_id]
    
    await callback_query.answer()

async def _delete_stale_message(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        logging.exception("Failed to remove stale egra message chat_id=%s", chat_id)
