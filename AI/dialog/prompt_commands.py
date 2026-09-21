"""Telegram-facing commands for dialogue personas and generated poems."""

import asyncio
import logging
import random

from aiogram import types

from core.loader import bot
from core.state import chat_settings
from features.chat_settings import save_chat_settings
from features.stat_rank_settings import get_user_display_name, get_valid_users
from prompts import (
    CUSTOM_PROMPT_TEMPLATE,
    PROMPT_PIROZHOK,
    PROMPT_POROSHOK,
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


_POEM_ACTIVE_POOL_SIZE = 8
_POEM_CHARACTER_COUNT = 4


def _rank_active_poem_users(valid_users: dict, *, limit: int = _POEM_ACTIVE_POOL_SIZE) -> list[str]:
    def count(stats: dict | None, key: str) -> int:
        return int((stats or {}).get(key, 0) or 0)

    def score(item):
        stats = item[1]
        return (
            count(stats, "weekly"),
            count(stats, "daily"),
            count(stats, "total"),
        )

    recent = [
        item
        for item in valid_users.items()
        if count(item[1], "weekly") > 0 or count(item[1], "daily") > 0
    ]
    candidates = recent or [
        item for item in valid_users.items() if count(item[1], "total") > 0
    ]
    ranked = sorted(candidates, key=score, reverse=True)
    return [str(user_id) for user_id, _stats in ranked[:limit]]


async def _get_dynamic_poem_characters(chat_id: str) -> str:
    try:
        valid_users = await get_valid_users(chat_id)
        user_ids = _rank_active_poem_users(valid_users)
        if not user_ids:
            return "случайные русские имена"

        names = await asyncio.gather(
            *(get_user_display_name(int(chat_id), int(user_id)) for user_id in user_ids)
        )
    except Exception as exc:
        logging.warning("Не удалось подобрать активных героев для стихов: %s", exc)
        return "случайные русские имена"

    unique_names = []
    seen = set()
    for raw_name in names:
        name = (raw_name or "").strip()
        if not name or name.startswith("Пользователь "):
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique_names.append(name)

    if not unique_names:
        return "случайные русские имена"

    selected = random.sample(unique_names, k=min(_POEM_CHARACTER_COUNT, len(unique_names)))
    return ", ".join(selected)


async def handle_poem_command(message: types.Message, poem_type: str):
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))
    logging.info("Обработчик для %r вызван", poem_type)

    parts = message.text.split(maxsplit=1)
    characters = parts[1].strip() if len(parts) > 1 else await _get_dynamic_poem_characters(chat_id)

    if poem_type == "пирожок":
        base_prompt = PROMPT_PIROZHOK[0]
        error_response = "🔥 Пирожок сгорел в духовке!"
    else:
        base_prompt = PROMPT_POROSHOK[0]
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
