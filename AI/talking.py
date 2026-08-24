"""Compatibility facade for the dialogue feature.

Production code imports focused modules from ``AI.dialog`` directly. This
module keeps the historical public API available while the repository finishes
the staged migration away from the old god-module.
"""

import logging

from aiogram import types
from aiogram.enums import ContentType

from config import bot, chat_settings, conversation_history, model
from core.upupa_utils import normalize_upupa_command
from features.chat_settings import add_chat, save_chat_settings
from features.lexicon_settings import save_user_message
from features.stat_rank_settings import track_message_statistics
from prompts import KEYWORDS
from services.web_context import get_web_context, needs_web_search

from AI.dialog.generation import (
    format_chat_history,
    generate_response,
    generate_simple_response,
    get_error_reply_text,
    handle_bot_conversation as _handle_bot_conversation,
    update_conversation_history,
)
from AI.dialog.model_commands import (
    handle_switch_to_gemini,
    handle_switch_to_gigachat,
    handle_switch_to_groq,
    handle_switch_to_history,
    handle_switch_to_openrouter,
    handle_switch_to_siliconflow,
    handle_which_model,
)
from AI.dialog.prompt_commands import (
    handle_change_prompt_randomly_command,
    handle_current_prompt_command,
    handle_list_prompts_command,
    handle_poem_command,
    handle_set_participant_prompt_command,
    handle_set_prompt_command,
)
from AI.dialog.serious_mode import handle_serious_mode_command, handle_serious_mode_reply
from AI.dialog.settings import (
    NO_CONFIDENCE_PERCENTAGES_INSTRUCTION,
    build_prompt_with_current_chat_prompt,
    get_current_chat_prompt,
    update_chat_settings,
)
from AI.dialog.style import create_user_style_prompt as _create_user_style_prompt
from AI.random_reactions import process_random_reactions


async def handle_bot_conversation(message: types.Message, user_first_name: str) -> str:
    """Compatibility wrapper preserving the old monkeypatch surface explicitly."""
    return await _handle_bot_conversation(
        message,
        user_first_name,
        generate_response_func=generate_response,
        needs_web_search_func=needs_web_search,
        get_web_context_func=get_web_context,
    )


async def process_general_message(message: types.Message):
    """Legacy composed entrypoint; canonical production flow lives elsewhere."""
    chat_id = str(message.chat.id)

    if await handle_serious_mode_reply(message):
        return

    update_chat_settings(chat_id)
    current_settings = chat_settings.get(chat_id, {})

    is_direct_appeal = False
    is_private_chat = message.chat.type == "private"
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user.id == bot.id
    is_unknown_bot_message = (
        message.from_user
        and message.from_user.is_bot
        and message.content_type == ContentType.UNKNOWN
        and not (message.text or message.caption)
    )

    if message.text:
        if message.text.lower().startswith("упупа"):
            text_lower = normalize_upupa_command(message.text)
        else:
            text_lower = message.text.lower()

        if (
            text_lower.startswith("пися")
            or any(
                keyword in text_lower.split()
                for keyword in [key.lower() for key in KEYWORDS if key not in ["пирожок", "порошок"]]
            )
        ):
            is_direct_appeal = True

        if not is_direct_appeal and message.entities:
            for entity in message.entities:
                if (
                    entity.type == "mention"
                    and message.text[entity.offset : entity.offset + entity.length]
                    == "@" + (await bot.get_me()).username
                ):
                    is_direct_appeal = True
                    break

    if (
        is_private_chat
        or is_reply_to_bot
        or is_direct_appeal
        or is_unknown_bot_message
    ) and current_settings.get("dialog_enabled", True):
        user_first_name = message.from_user.first_name or "Пользователь"
        await bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await handle_bot_conversation(message, user_first_name)
        await message.reply(response)
        return

    reaction_sent = await process_random_reactions(
        message,
        model,
        save_user_message,
        track_message_statistics,
        add_chat,
        chat_settings,
        save_chat_settings,
    )
    if reaction_sent:
        return

    logging.info(
        "Сообщение от %s в чате %s не вызвало реакции: %r",
        message.from_user.full_name,
        chat_id,
        message.text,
    )
