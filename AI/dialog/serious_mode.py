"""Serious-mode command and reply session handling."""

import logging
from datetime import datetime

from aiogram import types

from core.loader import bot
from core.state import cleanup_old_serious_messages, serious_mode_messages
from prompts import PROMPT_SERIOUS_MODE
from services.web_context import get_web_context, needs_web_search

from AI.dialog.generation import generate_simple_response


async def handle_serious_mode_command(message: types.Message):
    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action="typing")

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.reply(
            "Задай вопрос после 'упупа умоляю', например: упупа умоляю почему я такой пидорас?"
        )
        return

    user_question = parts[2].strip()
    if not user_question:
        await message.reply("Хули молчишь? Задай вопрос!")
        return

    web_context = ""
    if needs_web_search(user_question):
        try:
            web_context = await get_web_context(user_question)
        except Exception as exc:
            logging.warning("Web Search failed (serious mode): %s", exc)

    full_prompt = f"{PROMPT_SERIOUS_MODE}{web_context}\n\nВопрос: {user_question}"

    try:
        response_text = await generate_simple_response(full_prompt, chat_id)
        sent_message = await message.reply(response_text)
        serious_mode_messages[sent_message.message_id] = {
            "chat_id": chat_id,
            "timestamp": datetime.now(),
            "history": [
                {"role": "user", "content": user_question},
                {"role": "assistant", "content": response_text},
            ],
        }
    except Exception as exc:
        logging.error("Serious mode error: %s", exc)
        await message.reply("Ошибка при обработке запроса, попробуй ещё раз.")


async def handle_serious_mode_reply(message: types.Message) -> bool:
    if not message.reply_to_message:
        return False

    reply_msg_id = message.reply_to_message.message_id
    if reply_msg_id not in serious_mode_messages:
        return False

    cleanup_old_serious_messages()
    if reply_msg_id not in serious_mode_messages:
        return False

    chat_id = str(message.chat.id)
    await bot.send_chat_action(chat_id=chat_id, action="typing")

    session_data = serious_mode_messages[reply_msg_id]
    history = session_data.get("history", [])
    user_question = message.text.strip()
    history.append({"role": "user", "content": user_question})

    history_text = "\n".join(
        f"{'Пользователь' if msg['role'] == 'user' else 'Ты'}: {msg['content']}"
        for msg in history
    )
    full_prompt = (
        f"{PROMPT_SERIOUS_MODE}\n\n"
        f"История диалога:\n{history_text}\n\n"
        "Продолжи серьёзный и вдумчивый диалог, отвечая на последний вопрос пользователя."
    )

    try:
        response_text = await generate_simple_response(full_prompt, chat_id)
        sent_message = await message.reply(response_text)
        history.append({"role": "assistant", "content": response_text})
        serious_mode_messages[sent_message.message_id] = {
            "chat_id": chat_id,
            "timestamp": datetime.now(),
            "history": history,
        }
        return True
    except Exception as exc:
        logging.error("Serious mode reply error: %s", exc)
        await message.reply("Ошибка при обработке ответа.")
        return True
