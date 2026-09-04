"""Telegram-facing commands for dialogue personas and generated poems."""

import logging
import random

from aiogram import types

from core.loader import bot
from core.state import chat_settings
from features.chat_settings import save_chat_settings
from prompts import (
    CUSTOM_PROMPT_TEMPLATE,
    PROMPT_PIROZHOK,
    PROMPT_PIROZHOK1,
    PROMPT_POROSHOK,
    PROMPT_POROSHOK1,
    actions,
    get_available_prompts,
    get_prompt_by_name,
    get_prompts_list_text,
)

from AI.dialog.generation import generate_simple_response
from AI.dialog.participant_imitation import initialize_participant_profile
from AI.dialog.settings import update_chat_settings


def _clear_participant_metadata(settings: dict) -> None:
    settings.pop("imitated_user", None)
    settings.pop("style_profile_message_count", None)
    settings.pop("style_profile_updated_at", None)


async def handle_poem_command(message: types.Message, poem_type: str):
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))
    logging.info("Обработчик для %r вызван", poem_type)

    parts = message.text.lower().split(maxsplit=1)
    characters = parts[1] if len(parts) > 1 else "случайные русские имена"

    if poem_type == "пирожок":
        base_prompt = (
            PROMPT_PIROZHOK1[0]
            if message.chat.id == -1001707530786 and len(parts) == 1
            else PROMPT_PIROZHOK[0]
        )
        error_response = "🔥 Пирожок сгорел в духовке!"
    else:
        base_prompt = (
            PROMPT_POROSHOK1[0]
            if message.chat.id == -1001707530786 and len(parts) == 1
            else PROMPT_POROSHOK[0]
        )
        error_response = "💨 Порошок развеялся..."

    full_prompt = base_prompt + characters
    try:
        response_text = await generate_simple_response(full_prompt, chat_id)
    except Exception as exc:
        logging.error("API Error for %s: %s", poem_type, exc)
        response_text = error_response

    await message.reply(response_text)


async def handle_list_prompts_command(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    await message.reply(get_prompts_list_text())


async def handle_current_prompt_command(message: types.Message):
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))

    update_chat_settings(chat_id)
    current_settings = chat_settings.get(chat_id, {})
    current_prompt_name = current_settings.get("prompt_name")
    prompt_type = current_settings.get("prompt_type", "standard")

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
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))

    command_part = message.text[len("промпт ") :].strip()
    if not command_part:
        await message.reply("Нужно указать название готового промпта или написать свой текст.")
        return

    if command_part.lower() == "участник":
        await message.reply("Укажи участника: промпт участник [имя/@username]")
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
    _clear_participant_metadata(current_settings)
    save_chat_settings()
    await message.reply(reply_message)


async def handle_set_participant_prompt_command(message: types.Message):
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))

    command_part = message.text[len("промпт участник ") :].strip()
    if not command_part:
        await message.reply("Нужно указать имя или никнейм участника после команды.")
        return

    update_chat_settings(chat_id)
    current_settings = chat_settings[chat_id]
    identity = await initialize_participant_profile(chat_id, command_part, current_settings)

    if not identity:
        requested_name = command_part.lstrip("@")
        await message.reply(
            f"Не могу найти достаточно сообщений от пользователя '{requested_name}', чтобы ему подражать."
        )
        return

    save_chat_settings()
    display_name = identity["display_name"]
    await message.reply(
        f"Теперь я буду разговаривать как {display_name}! Буду подстраиваться под контекст."
    )


async def handle_change_prompt_randomly_command(message: types.Message):
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))

    available_prompts = get_available_prompts()
    if not available_prompts:
        await message.reply("Промпты не найдены, иди нахуй.")
        return

    current_prompt_name = chat_settings.get(chat_id, {}).get("prompt_name")
    possible_prompts = [name for name in available_prompts if name != "участник"]
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
    _clear_participant_metadata(current_settings)

    save_chat_settings()
    await message.reply(f"Теперь я {new_prompt_name} нахуй!")
