import asyncio
import random
import json
import re
from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message, PollAnswer
from config import model  # Импорт модели из твоего конфига

dnd_router = Router()

# Хранилище активных сессий: chat_id -> GameSession
dnd_sessions = {}

DND_SYSTEM_PROMPT = """
Ты — Мастер Подземелий (Dungeon Master) в текстовой RPG.
Твой характер: Ироничный, дерзкий, саркастичный, немного грубый. Ты используешь сленг и можешь позволить себе крепкое словцо.
Ты ведешь игру для участников чата. Интегрируй их имена в историю.

Твоя задача:
1. Генерировать короткие, но емкие куски сюжета (2-3 абзаца).
2. В конце сообщения ОБЯЗАТЕЛЬНО укажи один из технических тегов действий.

ФОРМАТ ТЕХНИЧЕСКИХ ТЕГОВ (В конце сообщения):

Если нужна развилка сюжета (Опрос):
[ACTION:POLL;OPTIONS:Вариант 1;Вариант 2;Вариант 3]
(Максимум 4 варианта. Используй часто).

Если нужна проверка навыка (Бросок кубика):
[ACTION:ROLL;STAT:Название характеристики (например, Ловкость)]

Если нужен ответ игрока текстом:
[ACTION:INPUT]

Если игрок попросил завершить игру ("упупа заверши историю"), опиши их нелепую или эпичную гибель и закончи текст тегом:
[ACTION:END]
"""

class GameSession:
    def __init__(self, chat_id, starter_name):
        self.chat_id = chat_id
        self.history = []
        self.chat_session = model.start_chat(history=[
            {"role": "user", "parts": [f"Начинай игру. Инициатор: {starter_name}. Следуй инструкциям по характеру и тегам."]},
            {"role": "model", "parts": ["Погнали, щенки. Сейчас устрою вам веселую жизнь."]}
        ])
        # Инъекция системного промпта в начало памяти (хак для сохранения персоналии)
        self.chat_session.history[0].parts[0].text = DND_SYSTEM_PROMPT + "\n\n" + self.chat_session.history[0].parts[0].text
        
        self.state = "WAITING_BACKSTORY" 
        self.last_roll_stat = None

async def parse_and_execute_turn(bot: Bot, chat_id: int, text_response: str):
    session = dnd_sessions.get(chat_id)
    if not session:
        return

    # Ищем тег действия
    action_match = re.search(r'\[ACTION:(.*?)\]', text_response)
    clean_text = re.sub(r'\[ACTION:.*?\]', '', text_response).strip()
    
    # Отправляем сюжетный текст
    if clean_text:
        await bot.send_message(chat_id, clean_text)

    if not action_match:
        # Если модель забыла тег, по дефолту ждем ввод
        session.state = "WAITING_ACTION"
        await bot.send_message(chat_id, "Ну, и че встали? (Жду действий...)")
        return

    command_str = action_match.group(1)
    
    # === ОБРАБОТКА ДЕЙСТВИЙ ===
    
    if command_str.startswith("POLL"):
        try:
            options_part = command_str.split("OPTIONS:")[1]
            options = [opt.strip() for opt in options_part.split(";")]
            # Обрезаем лишние варианты если их > 10 (ограничение ТГ) или пустые
            options = [o for o in options if o][:4] 
            
            session.state = "WAITING_POLL"
            
            # ВАЖНО: Убрали open_period, чтобы бот сам закрыл опрос через stop_poll
            poll_msg = await bot.send_poll(
                chat_id=chat_id,
                question="Чё делать будем?",
                options=options,
                is_anonymous=False
            )
            
            # Запускаем фоновую задачу ожидания
            asyncio.create_task(wait_for_poll_end(bot, chat_id, poll_msg.chat.id, poll_msg.message_id, options))
            
        except Exception as e:
            await bot.send_message(chat_id, f"(Мастер подавился кубиком: ошибка опроса. Просто напишите, что делаете).")
            session.state = "WAITING_ACTION"

    elif command_str.startswith("ROLL"):
        stat = command_str.split("STAT:")[1].strip()
        session.last_roll_stat = stat
        session.state = "WAITING_ROLL"
        await bot.send_message(chat_id, f"🎲 Проверка: *{stat}*. Пиши *кидаю*, чтобы не сдохнуть.", parse_mode="Markdown")

    elif command_str.startswith("INPUT"):
        session.state = "WAITING_ACTION"
        await bot.send_message(chat_id, "Ваши действия?")
        
    elif command_str.startswith("END"):
        del dnd_sessions[chat_id]
        await bot.send_message(chat_id, "☠️ Игра окончена. R.I.P.")

async def wait_for_poll_end(bot: Bot, chat_id: int, poll_chat_id: int, message_id: int, options: list):
    """Ждет 10 минут, стопает опрос, считает голоса и пинает модель"""
    await asyncio.sleep(600) # 600 секунд = 10 минут
    
    # Проверяем, жива ли сессия (могли отменить игру за это время)
    if chat_id not in dnd_sessions:
        try:
            await bot.stop_poll(chat_id=poll_chat_id, message_id=message_id)
        except:
            pass
        return

    outcome = "Никто не решился выбрать."
    try:
        # Останавливаем опрос и получаем результаты
        poll_res = await bot.stop_poll(chat_id=poll_chat_id, message_id=message_id)
        
        max_votes = 0
        winners = []
        
        for option in poll_res.options:
            if option.voter_count > max_votes:
                max_votes = option.voter_count
                winners = [option.text]
            elif option.voter_count == max_votes and max_votes > 0:
                winners.append(option.text)
        
        if not winners:
            random_choice = random.choice(options)
            outcome = f"Игроки промолчали. Случайность выбрала: {random_choice}"
        else:
            chosen = random.choice(winners)
            outcome = f"Большинство (или рандом при ничьей) выбрало: {chosen}"

        await bot.send_message(chat_id, f"⏳ Время вышло. {outcome}")
        
        # Отправляем выбор в модель
        session = dnd_sessions[chat_id]
        response = session.chat_session.send_message(f"Результат голосования: {outcome}. Продолжай историю.")
        await parse_and_execute_turn(bot, chat_id, response.text)
            
    except Exception as e:
        print(f"DnD Poll Error: {e}")
        # Если опрос сломался, просто пинаем модель, чтоб не висело
        session = dnd_sessions.get(chat_id)
        if session:
            response = session.chat_session.send_message("Опрос сломался, выбери любой вариант сам и продолжай.")
            await parse_and_execute_turn(bot, chat_id, response.text)

# ================== ХЭНДЛЕРЫ ==================

@dnd_router.message(F.text.lower().startswith("упупа начни историю"))
async def cmd_start_dnd(message: Message):
    user_name = message.from_user.first_name
    dnd_sessions[message.chat.id] = GameSession(message.chat.id, user_name)
    await message.answer(f"Так, {user_name}, решил поиграть с судьбой?\nЯ активирую режим Мастера.\n\nКакую предысторию хочешь, смертный? (Ответь реплаем на это сообщение)")

@dnd_router.message(F.text.lower().startswith("упупа заверши историю"))
async def cmd_stop_dnd(message: Message):
    session = dnd_sessions.get(message.chat.id)
    if not session:
        await message.answer("Да мы вроде и не играем, шизоид.")
        return
    
    await message.answer("Ой, всё? Надоело? Ладно, сейчас оформим красивый уход...")
    try:
        # Просим модель убить всех
        response = session.chat_session.send_message("Игроки просят завершить игру. Опиши короткий, саркастичный и летальный финал для всей группы. Используй тег [ACTION:END]")
        await parse_and_execute_turn(message.bot, message.chat.id, response.text)
    except Exception as e:
        await message.answer("Просто все умерли. Конец.")
        del dnd_sessions[message.chat.id]

@dnd_router.message(lambda m: m.reply_to_message and dnd_sessions.get(m.chat.id) and dnd_sessions[m.chat.id].state == "WAITING_BACKSTORY")
async def handle_backstory(message: Message):
    session = dnd_sessions[message.chat.id]
    backstory = message.text
    msg = await message.answer("Загружаю этот бред в матрицу...")
    
    try:
        response = session.chat_session.send_message(f"Предыстория: {backstory}. Начинай.")
        try: await message.bot.delete_message(message.chat.id, msg.message_id)
        except: pass
        await parse_and_execute_turn(message.bot, message.chat.id, response.text)
    except Exception as e:
        await message.answer(f"Ошибка нейронки: {e}")

@dnd_router.message(F.text.lower().contains("кидаю"))
async def handle_roll(message: Message):
    session = dnd_sessions.get(message.chat.id)
    # Реагируем только если ждем бросок
    if not session or session.state != "WAITING_ROLL":
        return 

    roll_result = random.randint(1, 20)
    stat = session.last_roll_stat
    
    comment = ""
    if roll_result == 1: comment = "(Критический провал! Земля тебе пухом)"
    elif roll_result == 20: comment = "(Критический успех! Читер?)"
    elif roll_result < 10: comment = "(Ну такое...)"
    
    await message.answer(f"🎲 {message.from_user.first_name} проверяет {stat}...\nВыпало: **{roll_result}** {comment}", parse_mode="Markdown")
    
    response = session.chat_session.send_message(f"Игрок {message.from_user.first_name} кинул на {stat}: результат {roll_result}. Описывай последствия.")
    await parse_and_execute_turn(message.bot, message.chat.id, response.text)

@dnd_router.message(lambda m: dnd_sessions.get(m.chat.id) and dnd_sessions[m.chat.id].state == "WAITING_ACTION")
async def handle_free_action(message: Message):
    if message.text.lower().startswith("упупа"): return
    
    session = dnd_sessions[message.chat.id]
    user_action = message.text
    user_name = message.from_user.first_name
    
    response = session.chat_session.send_message(f"Игрок {user_name} делает: {user_action}. Продолжай.")
    await parse_and_execute_turn(message.bot, message.chat.id, response.text)
