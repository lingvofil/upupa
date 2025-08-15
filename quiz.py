import asyncio
import logging
import random
import re
import json
from datetime import datetime, timedelta

import pytz
import aiofiles

from aiogram.types import Message, PollAnswer
from aiogram import Bot

# Обновленные импорты для Gemini
from config import LOG_FILE, quiz_questions, quiz_states, model


# Функция для получения временного диапазона
def get_time_range(days=1):
    moscow_tz = pytz.timezone('Europe/Moscow')
    now = datetime.now().astimezone(moscow_tz)
    
    if days == 1:
        start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = start_time + timedelta(days=1)
    else:
        end_time = now
        start_time = end_time - timedelta(days=days)
    
    return start_time, end_time

# Функция для извлечения сообщений из лог-файла
async def extract_messages(log_file, chat_id=None, limit=100, days=1):
    messages = []
    start_time, end_time = get_time_range(days)

    try:
        async with aiofiles.open(log_file, mode="r", encoding="utf-8") as f:
            log_lines = await f.readlines()

        for line in log_lines:
            match = re.search(r"(.*?) - Chat (.*?) \((.*?)\) - User (.*?) \((.*?)\) \[(.*?)\]: (.*)", line)
            if match:
                timestamp_str, msg_chat_id, chat_title, user_id, username, full_name, text = match.groups()
                
                msg_time = datetime.fromisoformat(timestamp_str)
                msg_time = pytz.utc.localize(msg_time).astimezone(pytz.timezone('Europe/Moscow'))
                
                if start_time <= msg_time <= end_time:
                    if not text or text.startswith('/') or (chat_id and str(chat_id) != str(msg_chat_id)):
                        continue

                    # Используем full_name, если оно не "NoName", иначе username
                    display_name = full_name if full_name != "NoName" else username
                    if display_name == "NoUsername": continue # Пропускаем, если нет имени

                    messages.append({
                        "text": text,
                        "user_id": user_id,
                        "username": username,
                        "full_name": display_name,
                        "chat_id": msg_chat_id,
                        "chat_title": chat_title,
                        "timestamp": timestamp_str
                    })

        return messages[-limit:] if len(messages) > limit else messages
    except Exception as e:
        logging.error(f"Ошибка при извлечении сообщений: {e}")
        return []

# Обновленная функция генерации вопросов с Gemini
async def generate_quiz_with_gemini(messages, num_questions=1):
    if not messages:
        return []
    
    sample_messages = random.sample(messages, min(500, len(messages)))
    messages_text = "\n".join([msg["text"] for msg in sample_messages])
    
    prompt = f"""
    Создай {num_questions} вопросов для викторины, основываясь на следующих сообщениях из чата.
    Каждый вопрос должен иметь 4 варианта ответа, из которых только один правильный.
    Вопросы должны быть основаны на содержании сообщений, их темах, упомянутых фактах, 
    используй нецензурную лексику, максимальный сарказм, хамство.
    
    Верни результат строго в формате JSON:
    [
      {{
        "text": "Текст вопроса",
        "options": ["Вариант 1", "Вариант 2", "Вариант 3", "Вариант 4"],
        "correct_answer": "Правильный вариант (должен совпадать с одним из вариантов в options)"
      }}
    ]
    
    Сообщения:
    {messages_text}
    """
    
    try:
        def sync_model_call():
            response = model.generate_content(prompt)
            return response.text

        response_text = await asyncio.to_thread(sync_model_call)
        
        json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
        json_str = json_match.group(1) if json_match else response_text.strip()
        
        questions = json.loads(json_str)
        
        validated_questions = [
            q for q in questions
            if isinstance(q, dict)
            and all(key in q for key in ('text', 'options', 'correct_answer'))
            and q['correct_answer'] in q['options']
        ]
        
        return validated_questions
        
    except Exception as e:
        logging.error(f"Ошибка при генерации вопросов: {e}")
        return [{
            'text': 'Не удалось сгенерировать вопросы. Что вам больше нравится?',
            'options': ['Пайти нахуй', 'Пайти фпизду', 'Попугаи', 'Золотой понос'],
            'correct_answer': 'Пайти нахуй'
        }]

# Функция для автоматической отправки викторины
async def send_daily_quiz(bot: Bot, chat_id: int):
    messages = await extract_messages(LOG_FILE, chat_id, days=1)
    
    if not messages:
        await bot.send_message(chat_id, "Недостаточно сообщений для создания викторины.")
        return

    questions = await generate_quiz_with_gemini(messages)
    
    if not questions:
        await bot.send_message(chat_id, "Не удалось создать викторину.")
        return

    quiz_questions[str(chat_id)] = questions
    await send_question(bot, chat_id, 0)

# Функция для запуска ежедневной викторины
async def schedule_daily_quiz(bot: Bot, chat_id: int):
    while True:
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now().astimezone(moscow_tz)
        
        target_time = now.replace(hour=23, minute=0, second=0, microsecond=0)
        if now >= target_time:
            target_time += timedelta(days=1)
        
        wait_seconds = (target_time - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        
        await send_daily_quiz(bot, chat_id)

# Обновленная функция send_question
async def send_question(bot, chat_id, question_index):
    try:
        chat_id_str = str(chat_id)
        if chat_id_str not in quiz_questions or question_index >= len(quiz_questions[chat_id_str]):
            quiz_states[chat_id_str] = None
            return
        
        questions = quiz_questions[chat_id_str]
        question = questions[question_index]
        
        logging.info(f"Отправка вопроса {question_index + 1} в чат {chat_id}")
        
        is_anonymous_quiz = True if chat_id_str == '-1001781970364' else False
        
        poll = await bot.send_poll(
            chat_id=chat_id,
            question=f"Вопрос {question_index + 1}/{len(questions)}\n{question['text']}",
            options=question['options'],
            type='quiz',
            correct_option_id=question['options'].index(question['correct_answer']),
            is_anonymous=is_anonymous_quiz,
            allows_multiple_answers=False
        )
        
        quiz_states[chat_id_str] = {
            'current_question': question_index,
            'poll_id': poll.poll.id
        }
        
        logging.info(f"Вопрос успешно отправлен, poll_id: {poll.poll.id}")
        
    except Exception as e:
        logging.error(f"Ошибка при отправке вопроса: {e}")
        await bot.send_message(chat_id, "Произошла ошибка при создании вопроса.")

# Вынесенная обработка "Викторина"
async def process_quiz_start(message: Message, bot: Bot) -> tuple[bool, str]:
    chat_id = message.chat.id
    chat_id_str = str(chat_id)
    
    logging.info(f"Получена команда викторины в чате {chat_id}")
    
    if quiz_states.get(chat_id_str) and chat_id_str != '-1001781970364':
        return False, "В этом чате уже идет викторина! Отъебись"
    
    try:
        messages = await extract_messages(LOG_FILE, chat_id, days=4)
        logging.info(f"Извлечено {len(messages)} сообщений для викторины")

        if not messages:
            return False, "Недостаточно сообщений для создания викторины. Общайтесь больше!"

        num_questions = 1 if chat_id_str == '-1001781970364' else 5
        
        questions = await generate_quiz_with_gemini(messages, num_questions)
        logging.info(f"Сгенерировано {len(questions)} вопросов")
        
        if not questions:
            return False, "Не удалось создать вопросы для викторины."

        quiz_questions[chat_id_str] = questions
        
        await send_question(bot, chat_id, 0)
        return True, ""
        
    except Exception as e:
        logging.error(f"Ошибка при запуске викторины: {e}")
        return False, "Произошла ошибка при создании викторины."

# Вынесенная обработка ответов
async def process_poll_answer(poll_answer: PollAnswer, bot: Bot) -> None:
    try:
        logging.info(f"Получен ответ на опрос: {poll_answer.poll_id}")
        
        chat_id_str = None
        quiz_state = None
        
        for current_chat_id, state in quiz_states.items():
            if state and state.get('poll_id') == poll_answer.poll_id:
                chat_id_str = current_chat_id
                quiz_state = state
                break
        
        if chat_id_str and quiz_state:
            logging.info(f"Найдена активная викторина в чате {chat_id_str}")
            
            await asyncio.sleep(3)
            
            await send_question(bot, int(chat_id_str), quiz_state['current_question'] + 1)
                
    except Exception as e:
        logging.error(f"Ошибка при обработке ответа на опрос: {e}")

# Новая функция для генерации викторины по участникам
async def generate_participant_quiz(messages, num_questions=5):
    if not messages:
        return []

    participants = {}
    for msg in messages:
        participants[msg['full_name']] = True 
    
    unique_participants = list(participants.keys())
    
    if len(unique_participants) < 2:
        return []

    sample_messages = random.sample(messages, min(500, len(messages)))
    messages_text = "\n".join([f"{msg['full_name']}: {msg['text']}" for msg in sample_messages])

    prompt = f"""
    Ты - ехидный и саркастичный создатель викторин.
    Проанализируй эти сообщения из чата:
    {messages_text}

    Твоя задача - создать викторину из {num_questions} вопросов.
    Каждый вопрос должен быть реальной, характерной, смешной или тупой фразой одного из участников.
    Для каждого вопроса создай от 2 до 4 вариантов ответа. Все варианты ответа ДОЛЖНЫ быть именами из этого списка: {', '.join(unique_participants)}. Не выдумывай несуществующих участников. Один из вариантов должен быть настоящим автором фразы.
    Вопрос должен быть в формате: "Кто из этих долбоёбов сказал: '[цитата]'?".
    Будь максимально дерзким, используй мат, если это уместно для стиля чата.

    Верни результат СТРОГО в формате JSON:
    [
      {{
        "text": "Текст вопроса с цитатой",
        "options": ["Имя участника 1", "Имя участника 2", "Имя реального автора"],
        "correct_answer": "Имя реального автора"
      }}
    ]
    """

    try:
        def sync_model_call():
            response = model.generate_content(prompt)
            return response.text

        response_text = await asyncio.to_thread(sync_model_call)
        
        json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
        json_str = json_match.group(1) if json_match else response_text.strip()
        
        questions = json.loads(json_str)
        
        validated_questions = [
            q for q in questions
            if isinstance(q, dict)
            and all(key in q for key in ('text', 'options', 'correct_answer'))
            and isinstance(q['options'], list) and len(q['options']) > 0
            and q['correct_answer'] in q['options']
        ]
        
        return validated_questions
        
    except Exception as e:
        logging.error(f"Ошибка при генерации викторины по участникам: {e}")
        return []


# Новая функция-обработчик для запуска викторины по участникам
async def process_participant_quiz_start(message: Message, bot: Bot) -> tuple[bool, str]:
    chat_id = message.chat.id
    chat_id_str = str(chat_id)

    logging.info(f"Получена команда 'викторина участники' в чате {chat_id}")

    if quiz_states.get(chat_id_str) and chat_id_str != '-1001781970364':
        return False, "Угомонись, тут уже идет другая викторина. Отъебись."

    try:
        messages = await extract_messages(LOG_FILE, chat_id, days=7)
        logging.info(f"Извлечено {len(messages)} сообщений для викторины по участникам")

        if len(messages) < 20:
            return False, "Слишком мало сообщений в чате, чтобы понять, кто тут что высирает. Общайтесь больше, далбаёбы!"

        questions = await generate_participant_quiz(messages, num_questions=5)
        
        if not questions:
            logging.error("Не удалось сгенерировать вопросы для викторины по участникам.")
            return False, "Не смог придумать вопросы. Видимо, вы все одинаково скучные."

        quiz_questions[chat_id_str] = questions
        await send_question(bot, chat_id, 0)
        return True, ""
        
    except Exception as e:
        logging.error(f"Критическая ошибка при запуске викторины по участникам: {e}")
        return False, "Что-то пошло по пизде при создании викторины."
