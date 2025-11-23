import asyncio
import random
import json
import re
from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message, PollAnswer
from config import model  # Импортируем настроенную модель из твоего конфига

dnd_router = Router()

# Хранилище активных сессий: chat_id -> GameSession
dnd_sessions = {}

# Системный промпт для Gemini, задающий характер мастера
DND_SYSTEM_PROMPT = """
Ты — Мастер Подземелий (Dungeon Master) в текстовой RPG.
Твой характер: Ироничный, дерзкий, саркастичный, немного грубый. Ты используешь сленг и можешь позволить себе крепкое словцо (нецензурную лексику в меру).
Ты ведешь игру для участников чата. Интегрируй их имена в историю.

Твоя задача:
1. Генерировать короткие, но емкие куски сюжета (2-3 абзаца).
2. В конце каждого своего сообщения ты ОБЯЗАН указать технический тег действия, чтобы программа поняла, что делать дальше.

ФОРМАТ ТЕХНИЧЕСКИХ ТЕГОВ (Пиши их в самом конце сообщения):

Если нужна развилка сюжета:
[ACTION:POLL;OPTIONS:Вариант 1;Вариант 2;Вариант 3]
(Максимум 4 варианта. Используй этот вариант часто для движухи).

Если нужна проверка навыка (игрок должен кинуть кубик):
[ACTION:ROLL;STAT:Название характеристики (например, Ловкость)]

Если нужен просто ответ игрока (диалог или свободное действие):
[ACTION:INPUT]

Пример ответа:
"Ну вы и, бл*ть, попали. Перед вами стоит огромный орк и ковыряет в зубах чьей-то берцовой костью. Он рыгает, и запах долетает до ваших носов.
[ACTION:POLL;OPTIONS:Атаковать в лоб;Попытаться украсть кость;Убежать с позором]"
"""

class GameSession:
    def __init__(self, chat_id, starter_name):
        self.chat_id = chat_id
        self.history = []
        self.chat_session = model.start_chat(history=[
            {"role": "user", "parts": [f"Начинай игру. Инициатор: {starter_name}. Следуй инструкциям по характеру и форматированию."]},
            {"role": "model", "parts": ["Окей, я готов унижать и властвовать. Жду вводную."]}
        ])
        # Добавляем системный промпт в контекст через первое сообщение или инструкцию (Gemini поддерживает system instruction при создании, но здесь делаем через chat для сохранения контекста)
        self.chat_session.history[0].parts[0].text = DND_SYSTEM_PROMPT + "\n" + self.chat_session.history[0].parts[0].text
        
        self.state = "WAITING_BACKSTORY" # WAITING_BACKSTORY, WAITING_POLL, WAITING_ROLL, WAITING_ACTION
        self.last_roll_stat = None

async def parse_and_execute_turn(bot: Bot, chat_id: int, text_response: str):
    session = dnd_sessions.get(chat_id)
    if not session:
        return

    # 1. Ищем тег действия
    action_match = re.search(r'\[ACTION:(.*?)\]', text_response)
    
    clean_text = re.sub(r'\[ACTION:.*?\]', '', text_response).strip()
    
    # Отправляем текст истории
    if clean_text:
        await bot.send_message(chat_id, clean_text)

    if not action_match:
        # Если Gemini забыл тег, по дефолту ждем ввод
        session.state = "WAITING_ACTION"
        await bot.send_message(chat_id, "Ну, и че встали? (Что делаете?)")
        return

    command_str = action_match.group(1)
    
    # === ЛОГИКА ГОЛОСОВАНИЯ ===
    if command_str.startswith("POLL"):
        options_str = command_str.split("OPTIONS:")[1]
        options = [opt.strip() for opt in options_str.split(";")]
        
        session.state = "WAITING_POLL"
        
        # Отправляем опрос
        poll_msg = await bot.send_poll(
            chat_id=chat_id,
            question="Чё делать будем?",
            options=options,
            is_anonymous=False,
            open_period=600 # 10 минут = 600 секунд
        )
        
        # Запускаем таймер ожидания конца опроса
        asyncio.create_task(wait_for_poll_end(bot, chat_id, poll_msg.chat.id, poll_msg.message_id, options))

    # === ЛОГИКА КУБИКА ===
    elif command_str.startswith("ROLL"):
        stat = command_str.split("STAT:")[1].strip()
        session.last_roll_stat = stat
        session.state = "WAITING_ROLL"
        await bot.send_message(chat_id, f"🎲 Проверка: *{stat}*. Пиши *кидаю*, чтобы испытать удачу.", parse_mode="Markdown")

    # === ЛОГИКА ВВОДА ===
    elif command_str.startswith("INPUT"):
        session.state = "WAITING_ACTION"
        await bot.send_message(chat_id, "Ваши действия?")

async def wait_for_poll_end(bot: Bot, chat_id: int, poll_chat_id: int, message_id: int, options: list):
    """Ждет 10 минут, закрывает опрос и продолжает историю"""
    await asyncio.sleep(600) # Ждем 10 минут
    
    try:
        poll_res = await bot.stop_poll(chat_id=poll_chat_id, message_id=message_id)
        
        # Считаем победителя
        max_votes = 0
        winners = []
        
        for option in poll_res.options:
            if option.voter_count > max_votes:
                max_votes = option.voter_count
                winners = [option.text]
            elif option.voter_count == max_votes and max_votes > 0:
                winners.append(option.text)
        
        if not winners:
            result_text = random.choice(options)
            outcome = f"Никто не проголосовал. Случайный выбор судьбы: {result_text}"
        else:
            result_text = random.choice(winners) # Если ничья, рандом среди победителей
            outcome = f"Народ решил: {result_text}"

        await bot.send_message(chat_id, f"⏳ Голосование окончено. {outcome}")
        
        # Продолжаем историю
        session = dnd_sessions.get(chat_id)
        if session:
            response = session.chat_session.send_message(f"Игроки выбрали: {outcome}. Продолжай историю.")
            await parse_and_execute_turn(bot, chat_id, response.text)
            
    except Exception as e:
        print(f"Error in poll wait: {e}")

# ================== ХЭНДЛЕРЫ ==================

@dnd_router.message(F.text.lower().startswith("упупа начни историю"))
async def cmd_start_dnd(message: Message):
    user_name = message.from_user.first_name
    dnd_sessions[message.chat.id] = GameSession(message.chat.id, user_name)
    
    await message.answer(f"Опа, {user_name}, приключений захотелось? Я активирую режим Бога.\nКакую историю бы ты хотел, буцефал? (Ответь реплаем на это сообщение)")

@dnd_router.message(lambda m: m.reply_to_message and dnd_sessions.get(m.chat.id) and dnd_sessions[m.chat.id].state == "WAITING_BACKSTORY")
async def handle_backstory(message: Message):
    session = dnd_sessions[message.chat.id]
    backstory = message.text
    
    wait_msg = await message.answer("Так-так, записываю... Генерирую мир дерьма и палок...")
    
    try:
        response = session.chat_session.send_message(f"Предыстория от игрока: {backstory}. Начинай сюжет.")
        await bot_delete_message(message.chat.id, wait_msg.message_id, message.bot) # Удаляем сообщение "генерирую"
        await parse_and_execute_turn(message.bot, message.chat.id, response.text)
    except Exception as e:
        await message.answer(f"Мой кремниевый мозг сбоит: {e}")

@dnd_router.message(F.text.lower() == "кидаю")
async def handle_roll(message: Message):
    session = dnd_sessions.get(message.chat.id)
    if not session or session.state != "WAITING_ROLL":
        return # Игнорим, если сейчас не время кидать

    roll_result = random.randint(1, 20)
    stat = session.last_roll_stat
    
    comment = ""
    if roll_result == 1: comment = "(Критический провал, лох!)"
    elif roll_result == 20: comment = "(Критический успех, красава!)"
    
    await message.answer(f"🎲 {message.from_user.first_name} кидает на {stat}...\nВыпало: **{roll_result}** {comment}", parse_mode="Markdown")
    
    # Отправляем результат мастеру
    response = session.chat_session.send_message(f"Игрок {message.from_user.first_name} кинул кубик на {stat}. Результат: {roll_result}. Опиши последствия.")
    await parse_and_execute_turn(message.bot, message.chat.id, response.text)

@dnd_router.message(lambda m: dnd_sessions.get(m.chat.id) and dnd_sessions[m.chat.id].state == "WAITING_ACTION")
async def handle_free_action(message: Message):
    # Обработка свободного ответа игрока, если это не команда старта
    if message.text.lower().startswith("упупа"): return
    
    session = dnd_sessions[message.chat.id]
    user_action = message.text
    user_name = message.from_user.first_name
    
    response = session.chat_session.send_message(f"Игрок {user_name} делает: {user_action}. Реагируй и двигай сюжет.")
    await parse_and_execute_turn(message.bot, message.chat.id, response.text)

async def bot_delete_message(chat_id, message_id, bot):
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass
