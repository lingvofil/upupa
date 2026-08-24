"""Telegram-facing commands for selecting the global dialogue model."""

import random

from aiogram import types

from config import (
    ADMIN_ID,
    bot,
    chat_settings,
    gigachat_model,
    groq_ai,
    model,
    openrouter_ai,
    siliconflow_ai,
)
from features.chat_settings import save_chat_settings
from prompts import actions

from AI.dialog.settings import update_chat_settings


async def handle_switch_to_gigachat(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return

    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "gigachat"
    save_chat_settings()
    await message.reply("🤖 Все чаты переключены на GigaChat")


async def handle_switch_to_gemini(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return

    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "gemini"
    save_chat_settings()
    await message.reply("✨ Все чаты переключены на Gemini")


async def handle_switch_to_groq(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return

    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "groq"
    save_chat_settings()
    await message.reply("⚡ Все чаты переключены на Groq")


async def handle_switch_to_history(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return

    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "history"
    save_chat_settings()
    await message.reply("📜 Все чаты переключены на режим 'По памяти'")


async def handle_switch_to_openrouter(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return

    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "openrouter"
    save_chat_settings()
    await message.reply(f"🚀 Переключил на OpenRouter {openrouter_ai.model_name}. Иди ты нахуй")


async def handle_switch_to_siliconflow(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("Эта команда доступна только администратору.")
        return

    for chat_id in chat_settings.keys():
        chat_settings[chat_id]["active_model"] = "siliconflow"
    save_chat_settings()
    await message.reply("🇨🇳 Переключил на SiliconFlow (DeepSeek V3.2). Силиконовая долина (лариса)")


async def handle_which_model(message: types.Message):
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
        "siliconflow": lambda: f"🇨🇳 Сейчас использую SiliconFlow: {siliconflow_ai.model_name}",
    }

    response = model_messages.get(active_model, model_messages["gemini"])()
    await message.reply(response)
