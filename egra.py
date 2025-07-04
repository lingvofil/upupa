import asyncio
import logging
from aiogram import types, Bot
from aiogram.exceptions import TelegramBadRequest

# Хранилище состояний игр для каждого чата
# Ключ - ID чата, значение - словарь с состоянием игры
game_states = {}

async def start_egra(message: types.Message, bot: Bot):
    """Начинает новую игру в чате."""
    chat_id = message.chat.id
    
    # Проверяем, не идет ли уже игра в этом чате
    if chat_id in game_states and game_states[chat_id].get("is_active", False):
        await message.reply("Мы уже и так еграем, ебана!))0")
        return
        
    # Инициализация состояния игры
    game_states[chat_id] = {
        "is_active": True,
        "options": ["1", "2", "3"],
        "poll_message_id": None,
        "poll_id": None,
    }
    
    await message.answer("Внеманее! Наченаем играть в прекольную егру!")
    
    # Отправляем первый опрос
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
            is_anonymous=False, # Важно, чтобы видеть, кто голосует
            allows_multiple_answers=False,
        )
        # Сохраняем ID сообщения и опроса для дальнейшего управления
        game["poll_message_id"] = poll_message.message_id
        game["poll_id"] = poll_message.poll.id
    except Exception as e:
        logging.error(f"Не удалось отправить опрос в чат {chat_id}: {e}")
        # Завершаем игру, если не можем отправить опрос
        del game_states[chat_id]


async def handle_egra_answer(poll_answer: types.PollAnswer, bot: Bot):
    """Обрабатывает ответ на опрос в игре."""
    # Ищем игру, к которой относится этот опрос
    game = None
    chat_id = None
    for cid, g in game_states.items():
        if g.get("poll_id") == poll_answer.poll_id:
            game = g
            chat_id = cid
            break
            
    # Если опрос не относится к нашей игре, выходим
    if not game:
        return False # Сигнал, что событие не обработано

    user = poll_answer.user
    # Получаем текст выбранного ответа. option_ids - это список индексов.
    chosen_option_index = poll_answer.option_ids[0]
    chosen_option_text = game["options"][chosen_option_index]

    # Удаляем старое сообщение с опросом
    try:
        await bot.delete_message(chat_id, game["poll_message_id"])
    except TelegramBadRequest as e:
        # Ошибки могут быть, если сообщение уже удалено или бот не имеет прав
        logging.warning(f"Не удалось удалить сообщение с опросом {game['poll_message_id']} в чате {chat_id}: {e}")

    # Удаляем выбранный вариант из списка
    game["options"].pop(chosen_option_index)

    # Проверяем, остались ли еще варианты
    if game["options"]:
        # Если варианты остались, продолжаем игру
        await bot.send_message(chat_id, f"{user.full_name} ножало \"{chosen_option_text}\".., прадолжаем..))00")
        await send_game_poll(chat_id, bot)
    else:
        # Если это был последний вариант - игра окончена
        await bot.send_message(chat_id, f"УРА! У НАС ПОБЕДИТЕЛЬ! ИГРА ОКОНЧЕНА! ПОЗДРАВЛЯЕМ, ТЫ КОНЧЕНЫ ХУЕСОС, {user.full_name}!")
        # Очищаем состояние игры
        del game_states[chat_id]
        
    return True # Сигнал, что событие успешно обработано

