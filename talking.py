import random
import logging
import asyncio
import os
import json
from aiogram import types

# Обновленные импорты
from config import (
    MAX_HISTORY_LENGTH, CHAT_SETTINGS_FILE, chat_settings,
    conversation_history, model, bot
)
# Функции для работы с файлами и промптами
from chat_settings import save_chat_settings, add_chat
from prompts import (
    PROMPTS_TEXT, PROMPTS_DICT, get_available_prompts,
    get_prompts_list_text, actions, get_prompt_by_name,
    PROMPT_PIROZHOK, PROMPT_PIROZHOK1, PROMPT_POROSHOK, PROMPT_POROSHOK1,
    KEYWORDS
)
# Функции для извлечения сообщений (предполагается, что они в Lexicon_settings.py)
from lexicon_settings import (save_user_message,
    extract_messages_by_username,
    extract_messages_by_full_name,
    get_frequent_phrases_from_text
)
# Импорт для реакций и статистики
from random_reactions import process_random_reactions
from stat_rank_settings import track_message_statistics


# =============================================================================
# НОВЫЕ ФУНКЦИИ-ОБРАБОТЧИКИ
# =============================================================================

async def handle_poem_command(message: types.Message, poem_type: str):
    """
    Универсальный обработчик для генерации стихов ('пирожок' или 'порошок').
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    logging.info(f"Обработчик для '{poem_type}' вызван")
    
    parts = message.text.lower().split(maxsplit=1)
    characters = parts[1] if len(parts) > 1 else "случайные русские имена"

    # Выбираем нужный промпт и сообщение об ошибке
    if poem_type == "пирожок":
        base_prompt = PROMPT_PIROZHOK1[0] if message.chat.id == -1001707530786 and len(parts) == 1 else PROMPT_PIROZHOK[0]
        error_response = "🔥 Пирожок сгорел в духовке!"
    else: # порошок
        base_prompt = PROMPT_POROSHOK1[0] if message.chat.id == -1001707530786 and len(parts) == 1 else PROMPT_POROSHOK[0]
        error_response = "💨 Порошок развеялся..."
        
    full_prompt = base_prompt + characters

    try:
        def sync_call():
            return model.generate_content(full_prompt).text
        response_text = await asyncio.to_thread(sync_call)
    except Exception as e:
        logging.error(f"Gemini API Error for {poem_type}: {e}")
        response_text = error_response

    await message.reply(response_text[:4000])


async def process_general_message(message: types.Message):
    """
    Главная функция, обрабатывающая все сообщения, которые не были перехвачены
    другими хэндлерами. Определяет, нужно ли вступать в диалог,
    реагировать или ничего не делать.
    """
    chat_id = str(message.chat.id)
    update_chat_settings(chat_id)
    current_settings = chat_settings.get(chat_id, {})

    # 1. Определяем, является ли это прямым обращением к боту
    is_direct_appeal = False
    is_private_chat = message.chat.type == "private"
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.id == bot.id
    
    if message.text:
        text_lower = message.text.lower()
        # Проверка на ключевые слова, упоминания и прямые обращения
        if (text_lower.startswith("пися ") or
            any(kw in text_lower.split() for kw in KEYWORDS if kw not in ["пирожок", "порошок"])):
            is_direct_appeal = True
        if not is_direct_appeal and message.entities:
            for entity in message.entities:
                if entity.type == "mention" and message.text[entity.offset:entity.offset + entity.length] == "@" + (await bot.get_me()).username:
                    is_direct_appeal = True
                    break

    # 2. Если это прямое обращение (или ЛС, или ответ боту), вступаем в диалог
    if (is_private_chat or is_reply_to_bot or is_direct_appeal) and current_settings.get("dialog_enabled", True):
        user_first_name = message.from_user.first_name or "Пользователь"
        await bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await handle_bot_conversation(message, user_first_name)
        await message.reply(response)
        return

    # 3. Если это не прямое обращение, пытаемся случайно среагировать
    if current_settings.get("dialog_enabled", True):
        reaction_sent = await process_random_reactions(
            message, model, save_user_message, track_message_statistics,
            add_chat, chat_settings, save_chat_settings
        )
        if reaction_sent:
            return

    # 4. Если ничего из вышеперечисленного не сработало
    logging.info(f"Сообщение от {message.from_user.full_name} в чате {chat_id} не вызвало реакции: '{message.text}'")


# =============================================================================
# СУЩЕСТВУЮЩИЕ ФУНКЦИИ УПРАВЛЕНИЯ ПРОМПТАМИ
# =============================================================================

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
        else:
            reply_text = f"Я {current_prompt_name}."
    else:
        current_prompt_text = current_settings.get("prompt")
        found_name = None
        if current_prompt_text:
            for name_key, text_val in PROMPTS_DICT.items():
                if text_val == current_prompt_text:
                    found_name = name_key
                    chat_settings[chat_id]["prompt_name"] = found_name
                    chat_settings[chat_id]["prompt_type"] = "standard"
                    save_chat_settings()
                    break
        if found_name:
            reply_text = f"Текущий промпт: {found_name} (имя восстановлено)."
        else:
            reply_text = "Текущий промпт не установлен. Можете установить его командой 'промпт <название>'."

    await message.reply(reply_text)


async def handle_set_prompt_command(message: types.Message):
    """
    Обрабатывает установку нового промпта, включая имитацию пользователя.
    """
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))

    command_part = message.text.lower().replace("промпт ", "").strip()
    if not command_part:
        await message.reply("Дебил, укажи 'промпт '. Например: промпт хуеплет или промпт @username")
        return

    is_user_imitation = command_part.startswith("@") or not get_prompt_by_name(command_part)

    if is_user_imitation:
        display_name = command_part[1:] if command_part.startswith("@") else command_part
        messages = (await extract_messages_by_username(display_name, chat_id) if command_part.startswith("@")
                    else await extract_messages_by_full_name(display_name, chat_id))

        if not messages:
            await message.reply(f"Не найдено ни сообщений от пользователя '{display_name}', ни стандартного промпта с таким именем.")
            return

        user_prompt = await _create_user_style_prompt(messages, display_name)
        update_chat_settings(chat_id)
        current_settings = chat_settings[chat_id]
        current_settings["prompt"] = user_prompt
        current_settings["prompt_name"] = display_name
        current_settings["prompt_source"] = "user_imitation"
        current_settings["prompt_type"] = "user_style"
        current_settings["imitated_user"] = {
            "username": display_name if command_part.startswith("@") else None,
            "full_name": display_name if not command_part.startswith("@") else None,
            "display_name": display_name
        }
        await message.reply(f"Теперь я буду разговаривать как {display_name}! Готов косплеить этого долбоеба.")
    else:
        new_prompt_text = get_prompt_by_name(command_part)
        update_chat_settings(chat_id)
        current_settings = chat_settings[chat_id]
        current_settings["prompt"] = new_prompt_text
        current_settings["prompt_name"] = command_part
        current_settings["prompt_source"] = "user"
        current_settings["prompt_type"] = "standard"
        if "imitated_user" in current_settings:
            del current_settings["imitated_user"]
        await message.reply(f"{command_part.capitalize()} в здании.")
    
    save_chat_settings()


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


# === Вспомогательные и основные функции диалога ===

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
        "\nПримеры сообщений:",
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
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {
            "dialog_enabled": True, "prompt": PROMPTS_DICT["врач"],
            "prompt_name": "летописец", "prompt_source": "daily"
        }

def get_current_chat_prompt(chat_id: str) -> tuple:
    update_chat_settings(chat_id)
    settings = chat_settings.get(chat_id, {})
    prompt_text = settings.get("prompt", PROMPTS_DICT["летописец"])
    prompt_name = settings.get("prompt_name", "летописец")
    return prompt_text, prompt_name

def update_conversation_history(user_id: int, message_text: str, role: str = "user"):
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    conversation_history[user_id].append({"role": role, "content": message_text})
    if len(conversation_history[user_id]) > MAX_HISTORY_LENGTH:
        conversation_history[user_id] = conversation_history[user_id][-MAX_HISTORY_LENGTH:]

def format_chat_history(user_id: int) -> str:
    if user_id not in conversation_history:
        return ""
    return "\n".join(f"{msg['role'].capitalize()}: {msg['content']}" for msg in conversation_history[user_id])

async def generate_response(prompt: str, user_id: int, **kwargs) -> str:
    try:
        def sync_gemini_call():
            response = model.generate_content(prompt)
            return response.text
        gemini_response_text = await asyncio.to_thread(sync_gemini_call)
        if not gemini_response_text.strip():
            gemini_response_text = "Я пока не знаю, что ответить... 😅"
        update_conversation_history(user_id, gemini_response_text, role="assistant")
        return gemini_response_text[:4000]
    except Exception as e:
        logging.error(f"Gemini API Error: {e}")
        error_message = "Ошибка блят"
        update_conversation_history(user_id, error_message, role="assistant")
        return error_message

async def handle_bot_conversation(message: types.Message, user_first_name: str) -> str:
    chat_id = str(message.chat.id)
    user_id = message.from_user.id
    user_input = message.text
    update_conversation_history(user_id, user_input, role="user")
    
    selected_prompt, _ = get_current_chat_prompt(chat_id)
    chat_history_formatted = format_chat_history(user_id)
    name_instruction = f"Важно: Сейчас ты общаешься с пользователем по имени '{user_first_name}'. Обращайся к нему по этому имени в своем ответе, если это уместно." if user_first_name else ""
    
    full_prompt = f"{selected_prompt}\n\n{name_instruction}\n\n{chat_history_formatted}\nAssistant:"
    
    response_text = await generate_response(full_prompt, user_id)
    return response_text
