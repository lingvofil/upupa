#talking.py
import random
import logging
import asyncio
import os
import json
from aiogram import types

# Импорт функции нормализации команд с "упупа"
from upupa_utils import normalize_upupa_command

# Обновленные импорты
from config import (
    MAX_HISTORY_LENGTH, CHAT_SETTINGS_FILE, chat_settings,
    conversation_history, model, gigachat_model, bot, groq_ai, ADMIN_ID, 
    serious_mode_messages, cleanup_old_serious_messages, 
    openrouter_ai, siliconflow_ai
)
# Функции для работы с файлами и промптами
from chat_settings import save_chat_settings, add_chat
from prompts import (
    PROMPTS_TEXT, PROMPTS_DICT, get_available_prompts,
    get_prompts_list_text, actions, get_prompt_by_name,
    PROMPT_PIROZHOK, PROMPT_PIROZHOK1, PROMPT_POROSHOK, PROMPT_POROSHOK1,
    KEYWORDS, CUSTOM_PROMPT_TEMPLATE,
    DIALOG_TRIGGER_KEYWORDS
)
# Функции для извлечения сообщений
from lexicon_settings import (
    save_user_message,
    extract_messages_by_username,
    extract_messages_by_full_name,
    extract_user_messages,
    get_frequent_phrases_from_text
)
# Импорт для реакций и статистики
from AI.random_reactions import process_random_reactions
from stat_rank_settings import track_message_statistics

# === ИМПОРТ SMART SEARCH И НОВОГО ENGINE ===
from smart_search import find_relevant_context
from history_engine import load_and_find_answer

# =============================================================================
# ОБРАБОТЧИКИ КОМАНД ПЕРЕКЛЮЧЕНИЯ МОДЕЛИ (ГЛОБАЛЬНО ДЛЯ ВСЕХ ЧАТОВ)
# =============================================================================

async def handle_switch_to_gigachat(message: types.Message):
    """Переключение на модель GigaChat для ВСЕХ чатов (только админ)"""
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return
    
    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "gigachat"
    save_chat_settings()
    await message.reply("🤖 Все чаты переключены на GigaChat")


async def handle_switch_to_gemini(message: types.Message):
    """Переключение на модель Gemini для ВСЕХ чатов (только админ)"""
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return
    
    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "gemini"
    save_chat_settings()
    await message.reply("✨ Все чаты переключены на Gemini")


async def handle_switch_to_groq(message: types.Message):
    """Переключение на модель Groq для ВСЕХ чатов (только админ)"""
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return
    
    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "groq"
    save_chat_settings()
    await message.reply("⚡ Все чаты переключены на Groq")

async def handle_switch_to_history(message: types.Message):
    """Переключение на режим истории (По памяти) для ВСЕХ чатов (только админ)"""
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return
    
    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "history"
    save_chat_settings()
    await message.reply("📜 Все чаты переключены на режим 'По памяти'")

async def handle_switch_to_openrouter(message: types.Message):
    """Переключение на OpenRouter для ВСЕХ чатов (только админ)"""
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return
    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "openrouter"
    save_chat_settings()
    await message.reply("🚀 Переключил на OpenRouter (Llama 3.3 70B Free). Иди ты нахуй")

async def handle_switch_to_siliconflow(message: types.Message):
    """Переключение на SiliconFlow для ВСЕХ чатов (только админ)"""
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return
    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "siliconflow"
    save_chat_settings()
    await message.reply("🇨🇳 Переключил на SiliconFlow (DeepSeek V3.2). Силиконовая долина (лариса)")

async def handle_which_model(message: types.Message):
    """Показывает текущую активную модель в этом чате"""
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))
    
    update_chat_settings(chat_id)
    current_settings = chat_settings.get(chat_id, {})
    active_model = current_settings.get("active_model", "gemini")
    
    model_messages = {
        "gigachat": lambda: f"🤖 Сейчас использую GigaChat: {gigachat_model.last_used_model_name or 'GigaChat-2'}",
        "history": lambda: "📜 Сейчас я в режиме 'По памяти' (использую историю логов)",
        "groq": lambda: f"⚡ Сейчас использую Groq: {groq_ai.text_model}",
        "gemini": lambda: f"✨ Сейчас использую Gemini: {model.last_used_model_name or 'gemini-2.0-flash'}",
        "openrouter": lambda: f"🚀 Сейчас использую OpenRouter: {openrouter_ai.model_name}",
        "siliconflow": lambda: f"🇨🇳 Сейчас использую SiliconFlow: {siliconflow_ai.model_name}"
    }
    
    response = model_messages.get(active_model, model_messages["gemini"])()
    await message.reply(response)

# =============================================================================
# ОБРАБОТЧИКИ КОМАНД (стихи, промпты)
# =============================================================================

async def generate_simple_response(prompt: str, chat_id: str) -> str:
    """Генерирует простой ответ без истории диалога (для пирожков, порошков и т.д.)"""
    try:
        update_chat_settings(chat_id)
        current_settings = chat_settings.get(chat_id, {})
        active_model = current_settings.get("active_model", "gemini")
        
        # Режим истории не подходит для генерации стихов
        if active_model == "history":
            active_model = "gemini"
        
        logging.info(f"generate_simple_response: используется модель {active_model}")
        logging.info(f"generate_simple_response: промпт = {prompt[:200]}...")  # Первые 200 символов
        
        def sync_model_call():
            if active_model == "gigachat":
                response = gigachat_model.generate_content(prompt, chat_id=int(chat_id))
                return response.text
            elif active_model == "groq":
                result = groq_ai.generate_text(prompt)
                logging.info(f"Groq вернул: '{result}'")
                return result
            elif active_model == "openrouter":
                result = openrouter_ai.generate_text(prompt)
                logging.info(f"OpenRouter вернул: '{result[:100] if result else ''}'")
                return result
            elif active_model == "siliconflow":
                result = siliconflow_ai.generate_text(prompt)
                logging.info(f"SiliconFlow вернул: '{result[:100] if result else ''}'")
                return result
            else:  # gemini
                response = model.generate_content(prompt, chat_id=int(chat_id))
                return response.text
        
        response_text = await asyncio.to_thread(sync_model_call)
        
        logging.info(f"generate_simple_response: получен ответ длиной {len(response_text)} символов")
        logging.info(f"generate_simple_response: ответ = '{response_text[:200]}'")
        
        if not response_text.strip():
            logging.warning("generate_simple_response: ответ пустой!")
            response_text = "Я пока не знаю, что ответить... 😅"
        
        return response_text[:4000]
        
    except Exception as e:
        logging.error(f"Model API Error in generate_simple_response: {e}", exc_info=True)
        return "Ошибка блят"


async def handle_poem_command(message: types.Message, poem_type: str):
    """
    Универсальный обработчик для генерации стихов ('пирожок' или 'порошок').
    """
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))
    logging.info(f"Обработчик для '{poem_type}' вызван")

    parts = message.text.lower().split(maxsplit=1)
    characters = parts[1] if len(parts) > 1 else "случайные русские имена"

    if poem_type == "пирожок":
        base_prompt = PROMPT_PIROZHOK1[0] if message.chat.id == -1001707530786 and len(parts) == 1 else PROMPT_PIROZHOK[0]
        error_response = "🔥 Пирожок сгорел в духовке!"
    else: # порошок
        base_prompt = PROMPT_POROSHOK1[0] if message.chat.id == -1001707530786 and len(parts) == 1 else PROMPT_POROSHOK[0]
        error_response = "💨 Порошок развеялся..."

    full_prompt = base_prompt + characters

    try:
        response_text = await generate_simple_response(full_prompt, chat_id)
    except Exception as e:
        logging.error(f"API Error for {poem_type}: {e}")
        response_text = error_response

    await message.reply(response_text)


async def handle_list_prompts_command(message: types.Message):
    """
    Обрабатывает команду 'промпты', отправляя список доступных промптов.
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompts_text = get_prompts_list_text()
    await message.reply(prompts_text)


async def handle_current_prompt_command(message: types.Message):
    """
    Обрабатывает команду 'какой промпт', сообщая текущую роль бота.
    """
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))

    update_chat_settings(chat_id)
    current_settings = chat_settings.get(chat_id, {})
    current_prompt_name = current_settings.get("prompt_name")
    prompt_type = current_settings.get("prompt_type", "standard")

    reply_text = ""
    if current_prompt_name:
        if prompt_type == "user_style":
            imitated_user = current_settings.get("imitated_user", {})
            display_name = imitated_user.get("display_name", current_prompt_name)
            reply_text = f"Я сейчас косплею {display_name} и разговариваю в его стиле."
        elif prompt_type == "custom":
            reply_text = "Ебать тебя не должно"
        else:
            reply_text = f"Я {current_prompt_name}."
    else:
        reply_text = "Текущий промпт не установлен."

    await message.reply(reply_text)

async def handle_set_prompt_command(message: types.Message):
    """
    Обрабатывает установку нового промпта (готового или кастомного).
    """
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))

    command_part = message.text[len("промпт "):].strip()
    if not command_part:
        await message.reply("Нужно указать название готового промпта или написать свой текст.")
        return

    predefined_prompt_text = get_prompt_by_name(command_part.lower())

    update_chat_settings(chat_id)
    current_settings = chat_settings[chat_id]

    if predefined_prompt_text:
        current_settings["prompt"] = predefined_prompt_text
        current_settings["prompt_name"] = command_part.lower()
        current_settings["prompt_type"] = "standard"
        reply_message = f"{command_part.capitalize()} в здании."
    else:
        full_custom_prompt = CUSTOM_PROMPT_TEMPLATE.format(personality=command_part)
        current_settings["prompt"] = full_custom_prompt
        current_settings["prompt_name"] = "кастомный"
        current_settings["prompt_type"] = "custom"
        reply_message = "Пошел нахуй! Ладно, принято"

    current_settings["prompt_source"] = "user"
    if "imitated_user" in current_settings:
        del current_settings["imitated_user"]
    save_chat_settings()
    await message.reply(reply_message)


async def handle_set_participant_prompt_command(message: types.Message):
    """
    Обрабатывает установку промпта для имитации участника чата.
    """
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))

    command_part = message.text[len("промпт участник "):].strip()
    if not command_part:
        await message.reply("Нужно указать имя или никнейм участника после команды.")
        return

    display_name = command_part.lstrip('@')

    # Ищем сообщения (сначала по юзернейму, потом по имени)
    messages = await extract_messages_by_username(display_name, chat_id)
    found_by = "username"
    if not messages:
        messages = await extract_messages_by_full_name(display_name, chat_id)
        found_by = "full_name"

    if not messages:
        await message.reply(f"Не могу найти сообщения от пользователя '{display_name}', чтобы ему подражать.")
        return

    # Создаем базовый промпт стиля (на основе частотных фраз)
    user_prompt = await _create_user_style_prompt(messages, display_name)
    
    update_chat_settings(chat_id)
    current_settings = chat_settings[chat_id]
    current_settings["prompt"] = user_prompt
    current_settings["prompt_name"] = display_name
    current_settings["prompt_source"] = "user_imitation"
    current_settings["prompt_type"] = "user_style"
    
    # Сохраняем метаданные пользователя, чтобы потом искать контекст
    current_settings["imitated_user"] = {
        "username": display_name if found_by == "username" else None,
        "full_name": display_name if found_by == "full_name" else None,
        "display_name": display_name
    }
    save_chat_settings()
    await message.reply(f"Теперь я буду разговаривать как {display_name}! Буду подстраиваться под контекст.")


async def handle_change_prompt_randomly_command(message: types.Message):
    """
    Обрабатывает команду 'поменяй промпт', устанавливая случайную роль.
    """
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))

    available_prompts = get_available_prompts()
    if not available_prompts:
        await message.reply("Промпты не найдены, иди нахуй.")
        return

    current_prompt_name = chat_settings.get(chat_id, {}).get("prompt_name")

    possible_prompts = list(available_prompts.keys())
    if len(possible_prompts) > 1 and current_prompt_name in possible_prompts:
        possible_prompts.remove(current_prompt_name)

    new_prompt_name = random.choice(possible_prompts)
    new_prompt_text = available_prompts[new_prompt_name]

    update_chat_settings(chat_id)
    current_settings = chat_settings[chat_id]
    current_settings["prompt"] = new_prompt_text
    current_settings["prompt_name"] = new_prompt_name
    current_settings["prompt_source"] = "user"
    current_settings["prompt_type"] = "standard"
    if "imitated_user" in current_settings:
        del current_settings["imitated_user"]

    save_chat_settings()
    await message.reply(f"Теперь я {new_prompt_name} нахуй!")

async def handle_serious_mode_command(message: types.Message):
    """
    Обрабатывает команду 'упупа умоляю' для серьёзного и вдумчивого ответа.
    """
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    
    # Извлекаем вопрос
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.reply("Задай вопрос после 'упупа умоляю', например: упупа умоляю почему я такой пидорас?")
        return
    
    user_question = parts[2].strip()
    if not user_question:
        await message.reply("Хули молчишь? Задай вопрос!")
        return
    
    # Используем серьёзный промпт
    from prompts import PROMPT_SERIOUS_MODE
    full_prompt = f"{PROMPT_SERIOUS_MODE}\n\nВопрос: {user_question}"
    
    try:
        response_text = await generate_simple_response(full_prompt, chat_id)
        sent_message = await message.reply(response_text)
        
        # Сохраняем данные о серьёзном диалоге с историей
        from datetime import datetime
        serious_mode_messages[sent_message.message_id] = {
            'chat_id': chat_id,
            'timestamp': datetime.now(),
            'history': [
                {'role': 'user', 'content': user_question},
                {'role': 'assistant', 'content': response_text}
            ]
        }
        
    except Exception as e:
        logging.error(f"Serious mode error: {e}")
        await message.reply("Ошибка при обработке запроса, попробуй ещё раз.")

async def handle_serious_mode_reply(message: types.Message) -> bool:
    """
    Обрабатывает реплай на сообщение в серьёзном режиме.
    Возвращает True, если сообщение было обработано в этом режиме.
    """
    if not message.reply_to_message:
        return False
    
    reply_msg_id = message.reply_to_message.message_id
    
    if reply_msg_id not in serious_mode_messages:
        return False
    
    # Очищаем старые записи перед обработкой
    cleanup_old_serious_messages()
    
    # Проверяем, что запись ещё существует после очистки
    if reply_msg_id not in serious_mode_messages:
        return False
    
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    
    session_data = serious_mode_messages[reply_msg_id]
    history = session_data.get('history', [])
    
    # Добавляем новый вопрос пользователя
    user_question = message.text.strip()
    history.append({'role': 'user', 'content': user_question})
    
    # Формируем промпт с историей диалога
    from prompts import PROMPT_SERIOUS_MODE
    
    history_text = "\n".join([
        f"{'Пользователь' if msg['role'] == 'user' else 'Ты'}: {msg['content']}"
        for msg in history
    ])
    
    full_prompt = (
        f"{PROMPT_SERIOUS_MODE}\n\n"
        f"История диалога:\n{history_text}\n\n"
        f"Продолжи серьёзный и вдумчивый диалог, отвечая на последний вопрос пользователя."
    )
    
    try:
        response_text = await generate_simple_response(full_prompt, chat_id)
        sent_message = await message.reply(response_text)
        
        # Обновляем историю
        history.append({'role': 'assistant', 'content': response_text})
        
        # Сохраняем новое сообщение с обновлённой историей
        from datetime import datetime
        serious_mode_messages[sent_message.message_id] = {
            'chat_id': chat_id,
            'timestamp': datetime.now(),
            'history': history
        }
        
        return True
        
    except Exception as e:
        logging.error(f"Serious mode reply error: {e}")
        await message.reply("Ошибка при обработке ответа.")
        return True

# =============================================================================
# ОСНОВНАЯ ЛОГИКА ДИАЛОГА
# =============================================================================

def update_conversation_history(chat_id: str, name: str, message_text: str, role: str):
    if chat_id not in conversation_history:
        conversation_history[chat_id] = []
    conversation_history[chat_id].append({"role": role, "name": name, "content": message_text})
    if len(conversation_history[chat_id]) > MAX_HISTORY_LENGTH:
        conversation_history[chat_id] = conversation_history[chat_id][-MAX_HISTORY_LENGTH:]

def format_chat_history(chat_id: str) -> str:
    if chat_id not in conversation_history or not conversation_history[chat_id]:
        return "Диалог только начинается."
    return "\n".join(f"{msg['name']}: {msg['content']}" for msg in conversation_history[chat_id])

async def generate_response(prompt: str, chat_id: str, bot_name: str, user_input: str = "") -> str:
    """Генерирует ответ с использованием выбранной модели (Gemini, GigaChat, Groq или История)"""
    try:
        update_chat_settings(chat_id)
        current_settings = chat_settings.get(chat_id, {})
        active_model = current_settings.get("active_model", "gemini")
        
        # --- РЕЖИМ "ПО ПАМЯТИ" (без нейросети) ---
        if active_model == "history":
            loop = asyncio.get_event_loop()
            ans = await loop.run_in_executor(None, load_and_find_answer, user_input, chat_id, 3)
            if ans:
                update_conversation_history(chat_id, bot_name, ans, role="assistant")
                return ans
            else:
                return "Отъебись"

        # --- ГЕНЕРАЦИЯ ЧЕРЕЗ НЕЙРОСЕТИ ---
        def sync_model_call():
            if active_model == "gigachat":
                response = gigachat_model.generate_content(prompt, chat_id=int(chat_id))
                return response.text
            elif active_model == "groq":
                return groq_ai.generate_text(prompt)
            elif active_model == "openrouter":
                result = openrouter_ai.generate_text(prompt)
                logging.info(f"OpenRouter вернул: '{result[:100] if result else ''}'")
                return result
            elif active_model == "siliconflow":
                result = siliconflow_ai.generate_text(prompt)
                logging.info(f"SiliconFlow вернул: '{result[:100] if result else ''}'")
                return result
            else:  # gemini
                response = model.generate_content(prompt, chat_id=int(chat_id))
                return response.text
        
        response_text = await asyncio.to_thread(sync_model_call)
        
        if not response_text.strip():
            response_text = "Я пока не знаю, что ответить... 😅"
        
        update_conversation_history(chat_id, bot_name, response_text, role="assistant")
        return response_text[:4000]
        
    except Exception as e:
        logging.error(f"Model API Error: {e}")
        error_message = "Ошибка блят"
        update_conversation_history(chat_id, bot_name, error_message, role="assistant")
        return error_message

async def handle_bot_conversation(message: types.Message, user_first_name: str) -> str:
    """
    Обрабатывает входящее сообщение в рамках общего диалога чата.
    """
    chat_id = str(message.chat.id)
    
    # Подготовка ввода пользователя
    user_input = message.text
    if not user_input or not isinstance(user_input, str):
        user_input = ""
    
    # Нормализуем для команд с "упупа"
    if user_input.lower().startswith('упупа'):
        temp_input_lower = normalize_upupa_command(user_input)
    else:
        temp_input_lower = user_input.lower()
    
    for keyword in DIALOG_TRIGGER_KEYWORDS:
        if temp_input_lower.startswith(keyword):
            user_input = user_input[len(keyword):].lstrip(' ,')
            break
    
    if not user_input.strip():
        return "Хули?"

    update_conversation_history(chat_id, user_first_name, user_input, role="user")
    
    # Получаем базовый промпт
    selected_prompt, prompt_name = get_current_chat_prompt(chat_id)
    
    # ===============================================================
    # SMART SEARCH: Если включена имитация, ищем релевантный контекст
    # ===============================================================
    current_settings = chat_settings.get(chat_id, {})
    additional_context = ""
    
    if current_settings.get("prompt_type") == "user_style":
        imitated_user_data = current_settings.get("imitated_user", {})
        
        # Определяем, кого мы имитируем, чтобы найти его логи
        target_name = imitated_user_data.get("username") or imitated_user_data.get("full_name")
        
        if target_name:
            if imitated_user_data.get("username"):
                messages = await extract_messages_by_username(imitated_user_data["username"], chat_id)
            else:
                messages = await extract_messages_by_full_name(imitated_user_data["full_name"], chat_id)
            
            if messages:
                relevant_msgs = await find_relevant_context(user_input, messages, top_k=3)
                
                if relevant_msgs:
                    additional_context = (
                        f"\n\nВАЖНО! Вот что {prompt_name} говорил(а) на похожие темы или в похожем контексте ранее:\n"
                        f"{' | '.join(relevant_msgs)}\n"
                        f"Используй эти фразы или мысли, чтобы ответ был максимально похож на него/неё."
                    )
                    logging.info(f"Smart Search added context for {prompt_name}: {relevant_msgs}")

    # ===============================================================

    chat_history_formatted = format_chat_history(chat_id)
    
    full_prompt = (
        f"{selected_prompt}\n"
        f"{additional_context}\n"
        f"Это текущий диалог в групповом чате. Твоя задача — органично его продолжить от лица '{prompt_name}'.\n"
        f"Вот история диалога:\n{chat_history_formatted}\n"
        f"{prompt_name}:"
    )
    
    # Передаем оригинальный user_input в generate_response для режима Истории
    response_text = await generate_response(full_prompt, chat_id, prompt_name, user_input=user_input)
    return response_text

async def process_general_message(message: types.Message):
    chat_id = str(message.chat.id)
    
    # ПРИОРИТЕТ 1: Проверяем серьёзный режим ПЕРВЫМ
    if await handle_serious_mode_reply(message):
        return
    
    update_chat_settings(chat_id)
    current_settings = chat_settings.get(chat_id, {})

    is_direct_appeal = False
    is_private_chat = message.chat.type == "private"
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.id == bot.id
    
    if message.text:
        # Нормализуем команды с "упупа"
        if message.text.lower().startswith('упупа'):
            text_lower = normalize_upupa_command(message.text)
        else:
            text_lower = message.text.lower()
        
        # Проверка на прямое обращение
        if (text_lower.startswith("пися") or
            any(kw in text_lower.split() for kw in [k.lower() for k in KEYWORDS if k not in ["пирожок", "порошок"]])):
            is_direct_appeal = True
            
        if not is_direct_appeal and message.entities:
            for entity in message.entities:
                if entity.type == "mention" and message.text[entity.offset:entity.offset + entity.length] == "@" + (await bot.get_me()).username:
                    is_direct_appeal = True
                    break

    if (is_private_chat or is_reply_to_bot or is_direct_appeal) and current_settings.get("dialog_enabled", True):
        user_first_name = message.from_user.first_name or "Пользователь"
        await bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await handle_bot_conversation(message, user_first_name)
        await message.reply(response)
        return

    reaction_sent = await process_random_reactions(
        message, model, save_user_message, track_message_statistics,
        add_chat, chat_settings, save_chat_settings
    )
    if reaction_sent:
        return

    logging.info(f"Сообщение от {message.from_user.full_name} в чате {chat_id} не вызвало реакции: '{message.text}'")


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (имитация стиля, настройки чатов)
# =============================================================================

async def _create_user_style_prompt(messages: list, display_name: str) -> str:
    """
    (Внутренняя функция) Создает промпт для имитации стиля пользователя.
    """
    sample_messages = random.sample(messages, min(200, len(messages)))
    all_text = " ".join(sample_messages)
    frequent_words = get_frequent_phrases_from_text(all_text, n=1, top_n=50)
    phrases_2 = get_frequent_phrases_from_text(all_text, n=2, top_n=10)
    phrases_3 = get_frequent_phrases_from_text(all_text, n=3, top_n=10)
    frequent_phrases = phrases_2 + phrases_3
    
    prompt_parts = [
        f"Ты должен имитировать стиль общения пользователя {display_name}.",
        "Анализируй следующие примеры его сообщений и копируй:",
        "- Манеру речи и словарный запас",
        "- Характерные выражения и обороты",
        "- Стиль юмора и тон общения",
        "\nПримеры сообщений (общий стиль):",
    ]
    for i, msg in enumerate(sample_messages[:15], 1):
        prompt_parts.append(f"{i}. {msg}")
    if frequent_words:
        prompt_parts.extend(["\nЧасто используемые слова:", ", ".join([word for word, _ in frequent_words])])
    if frequent_phrases:
        prompt_parts.append("\nХарактерные фразы:")
        for phrase, _ in frequent_phrases:
            prompt_parts.append(f"- {phrase}")
    prompt_parts.extend([
        "\nОтвечай ТОЧНО в том же стиле, используя похожие выражения и манеру речи.",
        "Будь естественным, как будто это действительно пишет этот человек.",
        "Ответ должен быть не более 50 слов."
    ])
    return "\n".join(prompt_parts)


def update_chat_settings(chat_id: str) -> None:
    """Инициализирует настройки для нового чата, если они отсутствуют."""
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {
            "dialog_enabled": True, 
            "reactions_enabled": True,
            "prompt": PROMPTS_DICT.get("врач", ""),
            "prompt_name": "летописец", 
            "prompt_source": "daily",
            "active_model": "gemini"  # Модель по умолчанию
        }

def get_current_chat_prompt(chat_id: str) -> tuple:
    update_chat_settings(chat_id)
    settings = chat_settings.get(chat_id, {})
    prompt_text = settings.get("prompt", PROMPTS_DICT.get("летописец", ""))
    prompt_name = settings.get("prompt_name", "летописец")
    return prompt_text, prompt_name
