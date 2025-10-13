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
    KEYWORDS, CUSTOM_PROMPT_TEMPLATE,
    DIALOG_TRIGGER_KEYWORDS  # <<< ДОБАВЛЕН ИМПОРТ
)
# Функции для извлечения сообщений
from lexicon_settings import (save_user_message,
    extract_messages_by_username,
    extract_messages_by_full_name,
    get_frequent_phrases_from_text
)
# Импорт для реакций и статистики
from random_reactions import process_random_reactions
from stat_rank_settings import track_message_statistics


# =============================================================================
# ОБРАБОТЧИКИ КОМАНД (Без изменений)
# =============================================================================

async def handle_poem_command(message: types.Message, poem_type: str):
    """
    Универсальный обработчик для генерации стихов ('пирожок' или 'порошок').
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
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
        def sync_call():
            return model.generate_content(full_prompt).text
        response_text = await asyncio.to_thread(sync_call)
    except Exception as e:
        logging.error(f"Gemini API Error for {poem_type}: {e}")
        response_text = error_response

    await message.reply(response_text[:4000])


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
    Эта функция быстрая, так как не ищет пользователей в истории.
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
    Эта функция может быть медленной из-за поиска по истории сообщений.
    """
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))

    command_part = message.text[len("промпт участник "):].strip()
    if not command_part:
        await message.reply("Нужно указать имя или никнейм участника после команды.")
        return

    display_name = command_part.lstrip('@')

    messages = await extract_messages_by_username(display_name, chat_id)
    if not messages:
        messages = await extract_messages_by_full_name(display_name, chat_id)

    if not messages:
        await message.reply(f"Не могу найти сообщения от пользователя '{display_name}', чтобы ему подражать.")
        return

    user_prompt = await _create_user_style_prompt(messages, display_name)
    update_chat_settings(chat_id)
    current_settings = chat_settings[chat_id]
    current_settings["prompt"] = user_prompt
    current_settings["prompt_name"] = display_name
    current_settings["prompt_source"] = "user_imitation"
    current_settings["prompt_type"] = "user_style"
    current_settings["imitated_user"] = {
        "username": display_name if '@' in command_part else None,
        "full_name": display_name if '@' not in command_part else None,
        "display_name": display_name
    }
    save_chat_settings()
    await message.reply(f"Теперь я буду разговаривать как {display_name}!")


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


# =============================================================================
# ОСНОВНАЯ ЛОГИКА ДИАЛОГА (ИЗМЕНЕНО)
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

async def generate_response(prompt: str, chat_id: str, bot_name: str) -> str:
    try:
        def sync_gemini_call():
            response = model.generate_content(prompt)
            return response.text
        gemini_response_text = await asyncio.to_thread(sync_gemini_call)
        if not gemini_response_text.strip():
            gemini_response_text = "Я пока не знаю, что ответить... 😅"
        update_conversation_history(chat_id, bot_name, gemini_response_text, role="assistant")
        return gemini_response_text[:4000]
    except Exception as e:
        logging.error(f"Gemini API Error: {e}")
        error_message = "Ошибка блят"
        update_conversation_history(chat_id, bot_name, error_message, role="assistant")
        return error_message

async def handle_bot_conversation(message: types.Message, user_first_name: str) -> str:
    """
    Обрабатывает входящее сообщение в рамках общего диалога чата.
    """
    chat_id = str(message.chat.id)
    
    # <<< НАЧАЛО ИЗМЕНЕНИЙ >>>
    user_input = message.text
    
    # Очищаем сообщение от триггерных слов, чтобы модель реагировала на суть
    temp_input_lower = user_input.lower()
    for keyword in DIALOG_TRIGGER_KEYWORDS:
        if temp_input_lower.startswith(keyword):
            # Удаляем ключевое слово и возможные пробелы/запятые после него
            user_input = user_input[len(keyword):].lstrip(' ,')
            break # Прерываем цикл, т.к. ключевое слово может быть только одно в начале
    
    # Если после очистки ничего не осталось, просим уточнить
    if not user_input.strip():
        return "Да, я вас слушаю. Что вы хотели спросить?"
    # <<< КОНЕЦ ИЗМЕНЕНИЙ >>>

    update_conversation_history(chat_id, user_first_name, user_input, role="user")
    
    selected_prompt, prompt_name = get_current_chat_prompt(chat_id)
    chat_history_formatted = format_chat_history(chat_id)
    
    full_prompt = (
        f"{selected_prompt}\n\n"
        f"Это текущий диалог в групповом чате. Твоя задача — органично его продолжить от лица '{prompt_name}'.\n"
        f"Вот история диалога:\n{chat_history_formatted}\n"
        f"{prompt_name}:"
    )
    
    response_text = await generate_response(full_prompt, chat_id, prompt_name)
    return response_text

async def process_general_message(message: types.Message):
    chat_id = str(message.chat.id)
    update_chat_settings(chat_id)
    current_settings = chat_settings.get(chat_id, {})

    is_direct_appeal = False
    is_private_chat = message.chat.type == "private"
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.id == bot.id
    
    if message.text:
        text_lower = message.text.lower()
        if (text_lower.startswith("пися ") or
            any(kw in text_lower.split() for kw in KEYWORDS if kw not in ["пирожок", "порошок"])):
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
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (Без изменений)
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
    """Инициализирует настройки для нового чата, если они отсутствуют."""
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {
            "dialog_enabled": True, 
            "reactions_enabled": True,
            "prompt": PROMPTS_DICT["врач"],
            "prompt_name": "летописец", 
            "prompt_source": "daily"
        }

def get_current_chat_prompt(chat_id: str) -> tuple:
    update_chat_settings(chat_id)
    settings = chat_settings.get(chat_id, {})
    prompt_text = settings.get("prompt", PROMPTS_DICT["летописец"])
    prompt_name = settings.get("prompt_name", "летописец")
    return prompt_text, prompt_name
