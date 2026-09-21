"""Telegram-facing commands for dialogue personas and generated poems."""

import asyncio
import logging
import random
import re

from aiogram import types

from core.loader import bot
from core.state import chat_settings
from features.chat_settings import save_chat_settings
from features.stat_rank_settings import get_user_display_name, get_valid_users
from features.statistics import get_chat_participant_activity
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
_POEM_ACTIVE_BOT_POOL_SIZE = 8
_POEM_PARTICIPANT_SCAN_LIMIT = 50
_TELEGRAM_FAKE_SENDER_USER_IDS = {777000, 1087968824}
_LATIN_TO_CYRILLIC_SEQUENCES = (
    ("shch", "щ"),
    ("sch", "щ"),
    ("yo", "ё"),
    ("zh", "ж"),
    ("kh", "х"),
    ("ts", "ц"),
    ("ch", "ч"),
    ("sh", "ш"),
    ("yu", "ю"),
    ("ya", "я"),
    ("ye", "е"),
)
_LATIN_TO_CYRILLIC_CHARS = {
    "a": "а",
    "b": "б",
    "c": "к",
    "d": "д",
    "e": "е",
    "f": "ф",
    "g": "г",
    "h": "х",
    "i": "и",
    "j": "й",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "q": "к",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "v": "в",
    "w": "в",
    "x": "кс",
    "y": "й",
    "z": "з",
}


def _normalize_poem_bot_name(raw_name: str | None) -> str:
    """Return one short Cyrillic bot name suitable for a poem prompt."""
    match = re.search(r"[A-Za-zА-Яа-яЁё]+", raw_name or "")
    if not match:
        return ""

    token = match.group(0)
    if not re.search(r"[A-Za-z]", token):
        return token[:1].upper() + token[1:].lower()

    source = token.casefold()
    result: list[str] = []
    index = 0
    while index < len(source):
        for latin, cyrillic in _LATIN_TO_CYRILLIC_SEQUENCES:
            if source.startswith(latin, index):
                result.append(cyrillic)
                index += len(latin)
                break
        else:
            result.append(_LATIN_TO_CYRILLIC_CHARS.get(source[index], source[index]))
            index += 1

    normalized = "".join(result)
    return normalized[:1].upper() + normalized[1:]




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


async def _get_active_poem_bot_names(
    chat_id: str,
    human_user_ids: set[str],
) -> list[str]:
    bot_names: list[str] = []
    seen: set[str] = set()

    try:
        me = await bot.get_me()
        own_name = _normalize_poem_bot_name(
            getattr(me, "first_name", None) or getattr(me, "full_name", None) or "Упупа"
        )
        if own_name:
            bot_names.append(own_name)
            seen.add(own_name.casefold())
    except Exception as exc:
        logging.warning("Не удалось получить имя Упупы для стихов: %s", exc)
        bot_names.append("Упупа")
        seen.add("упупа")

    try:
        activity = await get_chat_participant_activity(
            int(chat_id),
            period_hours=24 * 7,
            limit=_POEM_PARTICIPANT_SCAN_LIMIT,
        )
    except Exception as exc:
        logging.warning("Не удалось получить активность ботов для стихов: %s", exc)
        return bot_names[:_POEM_ACTIVE_BOT_POOL_SIZE]

    bot_candidates = [
        row
        for row in activity
        if str(row.get("user_id")) not in human_user_ids
    ]

    for row in bot_candidates:
        if len(bot_names) >= _POEM_ACTIVE_BOT_POOL_SIZE:
            break
        user_id = row.get("user_id")
        if not isinstance(user_id, int) or user_id in _TELEGRAM_FAKE_SENDER_USER_IDS:
            continue
        try:
            member = await bot.get_chat_member(int(chat_id), user_id)
        except Exception:
            continue

        user = getattr(member, "user", None)
        if not user or not getattr(user, "is_bot", False):
            continue
        name = _normalize_poem_bot_name(
            getattr(user, "first_name", None)
            or getattr(user, "full_name", None)
            or row.get("user_name")
            or row.get("user_username")
            or ""
        )
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        bot_names.append(name)

    return bot_names


def _format_poem_character_instruction(
    active_bot_names: list[str],
    other_characters: str,
) -> str:
    other_characters = (other_characters or "").strip()
    if not active_bot_names:
        return other_characters or "случайные русские имена"

    selected_bot = random.choice(active_bot_names)
    if other_characters:
        return (
            f"обязательный активный бот (должен появиться в тексте): {selected_bot}; "
            f"остальные герои: {other_characters}"
        )
    return f"обязательный активный бот (должен появиться в тексте): {selected_bot}"


async def _get_dynamic_poem_characters(chat_id: str) -> str:
    try:
        valid_users = await get_valid_users(chat_id)
        user_ids = _rank_active_poem_users(valid_users)
        names = await asyncio.gather(
            *(get_user_display_name(int(chat_id), int(user_id)) for user_id in user_ids)
        )
        active_bot_names = await _get_active_poem_bot_names(chat_id, set(valid_users))
    except Exception as exc:
        logging.warning("Не удалось подобрать активных героев для стихов: %s", exc)
        return "случайные русские имена"

    unique_names = []
    seen = {name.casefold() for name in active_bot_names}
    for raw_name in names:
        name = (raw_name or "").strip()
        if not name or name.startswith("Пользователь "):
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique_names.append(name)

    selected = random.sample(unique_names, k=min(_POEM_CHARACTER_COUNT, len(unique_names)))
    return _format_poem_character_instruction(
        active_bot_names,
        ", ".join(selected) if selected else "",
    )


async def handle_poem_command(message: types.Message, poem_type: str):
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action=random.choice(actions))
    logging.info("Обработчик для %r вызван", poem_type)

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        explicit_characters = parts[1].strip()
        try:
            valid_users = await get_valid_users(chat_id)
        except Exception as exc:
            logging.warning("Не удалось получить людей для исключения при поиске ботов: %s", exc)
            valid_users = {}
        active_bot_names = await _get_active_poem_bot_names(chat_id, set(valid_users))
        characters = _format_poem_character_instruction(active_bot_names, explicit_characters)
    else:
        characters = await _get_dynamic_poem_characters(chat_id)

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
