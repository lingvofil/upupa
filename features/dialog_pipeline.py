"""Explicit catch-all dialogue pipeline.

This module owns the production order of random reactions and direct dialogue.
Dialogue helpers come from focused ``AI.dialog`` modules; the legacy
``AI.random_reactions.process_random_reactions`` entrypoint remains available
for compatibility but production composes the reaction flow explicitly here.
"""

import logging

from aiogram.enums import ContentType
from aiogram.types import Message

import AI.random_reactions as random_reactions
import AI.situational_summary as situational_summary
from AI.dialog.generation import handle_bot_conversation
from AI.dialog.participant_imitation import record_participant_message
from AI.dialog.serious_mode import handle_serious_mode_reply
from AI.dialog.settings import update_chat_settings
from core.loader import bot
from core.state import chat_settings
from core.upupa_utils import normalize_upupa_command
from features.chat_settings import add_chat, save_chat_settings
from features.lexicon_settings import save_user_message
from features.stat_rank_settings import track_message_statistics
from prompts import KEYWORDS


async def _generate_live_situational_reaction(chat_id: int) -> str | None:
    """Generate the R3 situational summary from the dedicated live-chat buffer."""
    history = list(situational_summary._context_for_chat(chat_id))
    if not history:
        history = random_reactions.conversation_history.get(str(chat_id), [])

    return await situational_summary.generate_absurd_situational_reaction(
        chat_id,
        history,
        random_reactions.generate_with_model,
    )


async def process_random_reactions_once(message: Message) -> bool:
    """Run accounting and random reactions exactly once for one Telegram message."""
    if not situational_summary._register_incoming_message(message):
        return False

    if not message.from_user or message.from_user.is_bot:
        return False

    await save_user_message(message)
    record_participant_message(message)
    await track_message_statistics(message)
    add_chat(message.chat.id, message.chat.title, message.chat.username)

    chat_id = str(message.chat.id)
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {
            "dialog_enabled": True,
            "reactions_enabled": True,
            "emoji_enabled": True,
        }
        save_chat_settings()

    chat_cfg = chat_settings.get(chat_id, {})

    if chat_cfg.get("emoji_enabled", True):
        emoji_prob = chat_cfg.get("emoji_prob", 0.01)
        if random_reactions.random.random() < emoji_prob:
            try:
                await random_reactions.set_random_emoji_reaction(message)
            except Exception as exc:
                logging.error("Emoji reaction failed: %s", exc, exc_info=True)

    if not chat_cfg.get("reactions_enabled", True):
        return False

    ai_prob = chat_cfg.get("ai_prob", 0.01)
    if random_reactions.random.random() < ai_prob:
        situational = await _generate_live_situational_reaction(message.chat.id)
        if situational:
            await message.bot.send_message(
                message.chat.id,
                situational,
                parse_mode="Markdown",
            )
            return True

    random_word_prob = chat_cfg.get("random_word_prob", 0.005)
    if random_reactions.random.random() < random_word_prob:
        random_word_reaction = await random_reactions.generate_random_word_reaction(message.chat.id)
        if random_word_reaction:
            await message.bot.send_message(message.chat.id, random_word_reaction)
            return True

    if message.from_user.id == 1399269377 and message.text and random_reactions.random.random() < 0.3:
        if await random_reactions.generate_insult_for_lis(message):
            return True

    if message.from_user.id == 113086922 and random_reactions.random.random() < 0.005:
        if await random_reactions.generate_reaction_for_113086922(message):
            return True

    voice_prob = chat_cfg.get("voice_prob", 0.0001)
    if message.voice and random_reactions.random.random() < 0.001:
        if await random_reactions.send_random_voice_reaction(message):
            return True

    if random_reactions.random.random() < voice_prob:
        if await random_reactions.send_random_common_voice_reaction(message):
            return True

    if message.text and "пара дня" in message.text.lower() and random_reactions.random.random() < 0.05:
        if await random_reactions.send_para_voice_reaction(message):
            return True

    rhyme_prob = chat_cfg.get("rhyme_prob", 0.008)
    if message.text and random_reactions.random.random() < rhyme_prob:
        rhyme = await random_reactions.generate_rhyme_reaction(message)
        if rhyme:
            await message.reply(rhyme)
            return True

    regular_prob = chat_cfg.get("regular_prob", 0.008)
    if message.text and random_reactions.random.random() < regular_prob:
        regular = await random_reactions.generate_regular_reaction(message)
        if regular:
            await message.reply(regular)
            return True

    return False


async def process_general_dialog_message(message: Message) -> None:
    """Handle serious/direct dialogue without launching reactions again."""
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

    logging.info(
        "Сообщение от %s в чате %s не вызвало реакции: %r",
        message.from_user.full_name,
        chat_id,
        message.text,
    )


async def process_dialog_pipeline(message: Message) -> bool:
    """Run the canonical reaction -> direct-dialog sequence."""
    if await process_random_reactions_once(message):
        return True

    await process_general_dialog_message(message)
    return False
