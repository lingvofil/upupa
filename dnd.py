import asyncio
import random
import re
from aiogram import Router, F, Bot
from aiogram.types import Message, PollAnswer
from config import model  # Импорт модели из твоего конфига

dnd_router = Router()

# Хранилище активных сессий: chat_id -> GameSession
dnd_sessions = {}
# Хранилище связи опроса с чатом: poll_id -> chat_id (нужно для PollAnswer)
poll_map = {}

DND_SYSTEM_PROMPT = """
Ты — Мастер Подземелий (Dungeon Master) в текстовой RPG.
Твой характер: Ироничный, дерзкий, саркастичный, немного грубый. Ты используешь сленг.

Твоя задача:
1. Генерировать ОЧЕНЬ КОРОТКИЕ куски сюжета (СТРОГО до 100 слов). Не лей воду.
2. В конце сообщения ОБЯЗАТЕЛЬНО укажи один из технических тегов действий.

ФОРМАТ ТЕХНИЧЕСКИХ ТЕГОВ (В конце сообщения):

Если нужна развилка сюжета (Опрос):
[ACTION:POLL;OPTIONS:Вариант 1;Вариант 2;Вариант 3]
(Максимум 4 варианта).

Если нужна проверка навыка (Бросок кубика):
[ACTION:ROLL;STAT:Название характеристики]

Если нужен ответ игрока текстом:
[ACTION:INPUT]

Если игрок попросил завершить игру, опиши гибель и закончи тегом:
[ACTION:END]
"""

class GameSession:
    def __init__(self, chat_id, starter_name):
        self.chat_id = chat_id
        self.history = []
        # Инициализация чата с моделью, передаем chat_id для балансировки нагрузки
        self.chat_session = model.start_chat(
            chat_id=chat_id, # <<< ИЗМЕНЕНИЕ 1/6
            history=[
                {"role": "user", "parts": [f"Начинай игру. Инициатор: {starter_name}. Помни: не более 100 слов."]},
                {"role": "model", "parts": ["Погнали."]}
            ]
        )
        # Инъекция системного промпта
        self.chat_session.history[0].parts[0].text = DND_SYSTEM_PROMPT + "\n\n" + self.chat_session.history[0].parts[0].text
        
        self.state = "WAITING_BACKSTORY" 
        self.last_roll_stat = None
        
        # Переменные для логики опросов
        self.current_poll_id = None
        self.poll_has_votes = False
        self.waiting_for_first_vote = False # Флаг режима ожидания после 10 минут

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
        session.state = "WAITING_ACTION"
        await bot.send_message(chat_id, "Жду действий...")
        return

    command_str = action_match.group(1)
    
    # === ОБРАБОТКА ДЕЙСТВИЙ ===
    
    if command_str.startswith("POLL"):
        try:
            options_part = command_str.split("OPTIONS:")[1]
            options = [opt.strip() for opt in options_part.split(";")]
            options = [o for o in options if o][:4] 
            
            session.state = "WAITING_POLL"
            session.poll_has_votes = False
            session.waiting_for_first_vote = False
            
            poll_msg = await bot.send_poll(
                chat_id=chat_id,
                question="Чё делать будем?",
                options=options,
                is_anonymous=False # Важно False, чтобы ловить PollAnswer
            )
            
            # Сохраняем ID опроса
            session.current_poll_id = str(poll_msg.poll.id)
            poll_map[str(poll_msg.poll.id)] = chat_id
            
            # Запускаем таймер
            asyncio.create_task(wait_for_poll_timeout(bot, chat_id, poll_msg.chat.id, poll_msg.message_id, options, str(poll_msg.poll.id)))
            
        except Exception as e:
            await bot.send_message(chat_id, f"(Ошибка опроса. Пишите текстом).")
            session.state = "WAITING_ACTION"

    elif command_str.startswith("ROLL"):
        stat = command_str.split("STAT:")[1].strip()
        session.last_roll_stat = stat
        session.state = "WAITING_ROLL"
        await bot.send_message(chat_id, f"🎲 Проверка: *{stat}*. Пиши *кидаю*.", parse_mode="Markdown")

    elif command_str.startswith("INPUT"):
        session.state = "WAITING_ACTION"
        await bot.send_message(chat_id, "Ваши действия?")
        
    elif command_str.startswith("END"):
        cleanup_session(chat_id)
        await bot.send_message(chat_id, "☠️ Игра окончена.")

def cleanup_session(chat_id):
    """Удаляет сессию и чистит карту опросов"""
    if chat_id in dnd_sessions:
        session = dnd_sessions[chat_id]
        if session.current_poll_id and session.current_poll_id in poll_map:
            del poll_map[session.current_poll_id]
        del dnd_sessions[chat_id]

async def finalize_poll(bot: Bot, chat_id: int, message_id: int, options: list):
    """Останавливает опрос, считает голоса и продолжает историю"""
    session = dnd_sessions.get(chat_id)
    if not session: return

    try:
        poll_res = await bot.stop_poll(chat_id=chat_id, message_id=message_id)
        
        max_votes = 0
        winners = []
        
        for option in poll_res.options:
            if option.voter_count > max_votes:
                max_votes = option.voter_count
                winners = [option.text]
            elif option.voter_count == max_votes and max_votes > 0:
                winners.append(option.text)
        
        # Если голосов нет (хотя этот метод вызывается, когда они должны быть), берем рандом
        if not winners:
            outcome = f"Тишина... Случайность выбрала: {random.choice(options)}"
        else:
            outcome = f"Выбор сделан: {random.choice(winners)}"

        await bot.send_message(chat_id, f"✅ {outcome}")
        
        # Очищаем карту опроса
        if session.current_poll_id in poll_map:
            del poll_map[session.current_poll_id]
        session.current_poll_id = None

        # Отправляем результат в модель, передаем chat_id
        response = session.chat_session.send_message(
            f"Результат: {outcome}. Продолжай (до 100 слов).",
            chat_id=chat_id # <<< ИЗМЕНЕНИЕ 2/6
        )
        await parse_and_execute_turn(bot, chat_id, response.text)
            
    except Exception as e:
        print(f"Poll Error: {e}")
        # Если не смогли стопнуть, просто пинаем модель
        response = session.chat_session.send_message(
            "Опрос завершен. Продолжай.",
            chat_id=chat_id # <<< ИЗМЕНЕНИЕ 3/6 (на случай ошибки)
        )
        await parse_and_execute_turn(bot, chat_id, response.text)

async def wait_for_poll_timeout(bot: Bot, chat_id: int, poll_chat_id: int, message_id: int, options: list, poll_id: str):
    """Ждет 10 минут. Если голосов нет — ждет первого героя."""
    await asyncio.sleep(600) # 10 минут
    
    session = dnd_sessions.get(chat_id)
    if not session or session.current_poll_id != poll_id:
        return # Сессия умерла или опрос уже другой

    if session.poll_has_votes:
        # Если голоса уже есть, завершаем штатно
        await finalize_poll(bot, chat_id, message_id, options)
    else:
        # Голосов нет. Включаем режим ожидания.
        session.waiting_for_first_vote = True
        # Сохраняем данные, чтобы finalize_poll мог их использовать позже
        session.pending_poll_data = {'message_id': message_id, 'options': options}
        
        await bot.send_message(chat_id, "⏳ 10 минут прошло, а вы молчите. Сюжет на паузе, пока кто-нибудь не нажмет кнопку.")

# ================== ХЭНДЛЕРЫ ==================

@dnd_router.poll_answer(lambda event: event.poll_id in poll_map)
async def handle_poll_answer(poll_answer: PollAnswer, bot: Bot):
    poll_id = poll_answer.poll_id
    chat_id = poll_map.get(poll_id)
    
    if not chat_id or chat_id not in dnd_sessions:
        return

    session = dnd_sessions[chat_id]
    session.poll_has_votes = True

    # Если мы в режиме ожидания "первого смельчака"
    if session.waiting_for_first_vote:
        session.waiting_for_first_vote = False # Сбрасываем флаг
        data = getattr(session, 'pending_poll_data', None)
        if data:
            # Сразу завершаем опрос, так как первый голос получен
            await finalize_poll(bot, chat_id, data['message_id'], data['options'])

@dnd_router.message(F.text.lower().startswith("упупа начни историю"))
async def cmd_start_dnd(message: Message):
    user_name = message.from_user.first_name
    cleanup_session(message.chat.id) # Чистим старую если была
    # chat_id передается в конструктор GameSession
    dnd_sessions[message.chat.id] = GameSession(message.chat.id, user_name)
    await message.answer(f"Лады, {user_name}. Какую предысторию хочешь? (Ответь реплаем)")

@dnd_router.message(F.text.lower().startswith(("упупа заверши историю", "упупа закончи историю")))
async def cmd_stop_dnd(message: Message):
    session = dnd_sessions.get(message.chat.id)
    if not session:
        await message.answer("Мы и не играем.")
        return
    
    try:
        # Передаем chat_id при запросе на завершение
        response = session.chat_session.send_message(
            "Игроки хотят конец игры. Опиши короткий финал с тегом [ACTION:END]",
            chat_id=message.chat.id # <<< ИЗМЕНЕНИЕ 4/6
        )
        await parse_and_execute_turn(message.bot, message.chat.id, response.text)
    except:
        cleanup_session(message.chat.id)
        await message.answer("Игра окончена.")

@dnd_router.message(lambda m: m.reply_to_message and dnd_sessions.get(m.chat.id) and dnd_sessions[m.chat.id].state == "WAITING_BACKSTORY")
async def handle_backstory(message: Message):
    session = dnd_sessions[message.chat.id]
    backstory = message.text
    msg = await message.answer("Генерирую...")
    
    try:
        # Передаем chat_id при отправке предыстории
        response = session.chat_session.send_message(
            f"Предыстория: {backstory}. Начинай.",
            chat_id=message.chat.id # <<< ИЗМЕНЕНИЕ 5/6
        )
        try: await message.bot.delete_message(message.chat.id, msg.message_id)
        except: pass
        await parse_and_execute_turn(message.bot, message.chat.id, response.text)
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

@dnd_router.message(F.text.lower().contains("кидаю"))
async def handle_roll(message: Message):
    session = dnd_sessions.get(message.chat.id)
    if not session or session.state != "WAITING_ROLL":
        return 

    roll_result = random.randint(1, 20)
    stat = session.last_roll_stat
    
    await message.answer(f"🎲 {message.from_user.first_name}: {stat} -> **{roll_result}**", parse_mode="Markdown")
    
    # Передаем chat_id при отправке результата броска
    response = session.chat_session.send_message(
        f"Игрок кинул на {stat}: {roll_result}. Продолжай.",
        chat_id=message.chat.id # <<< ИЗМЕНЕНИЕ 6/6
    )
    await parse_and_execute_turn(message.bot, message.chat.id, response.text)

@dnd_router.message(lambda m: dnd_sessions.get(m.chat.id) and dnd_sessions[m.chat.id].state == "WAITING_ACTION")
async def handle_free_action(message: Message):
    if message.text.lower().startswith("упупа"): return
    
    session = dnd_sessions[message.chat.id]
    user_action = message.text
    user_name = message.from_user.first_name
    
    # Передаем chat_id при отправке свободного действия
    response = session.chat_session.send_message(
        f"{user_name}: {user_action}. Продолжай.",
        chat_id=message.chat.id # <<< ИЗМЕНЕНИЕ 7/7
    )
    await parse_and_execute_turn(message.bot, message.chat.id, response.text)
