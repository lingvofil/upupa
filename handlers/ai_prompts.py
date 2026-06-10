"""Хэндлеры: Режим болтовни, промпты, пирожки, умоляю.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

from aiogram import Bot, Dispatcher, F, types
from config import (
    bot, ADMIN_ID, BLOCKED_USERS, conversation_history, model,
    chat_settings, chat_list, sms_disabled_chats, LOG_FILE
)
from core.upupa_utils import normalize_upupa_command
from features.chat_settings import (
    process_update_all_chats, get_chats_list, add_chat, save_chat_settings, remove_chat
)
from AI.talking import (
    handle_list_prompts_command,
    handle_current_prompt_command,
    handle_set_prompt_command,
    handle_set_participant_prompt_command,
    handle_change_prompt_randomly_command,
    handle_poem_command,
    process_general_message,
    handle_switch_to_gigachat,
    handle_switch_to_gemini,
    handle_switch_to_groq,
    handle_which_model,
    handle_switch_to_history,
    handle_serious_mode_command,
    handle_serious_mode_reply,
    handle_switch_to_openrouter,
    handle_switch_to_siliconflow
)

router = Router(name="ai_prompts")


@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа не болтай")
async def disable_dialog(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {"dialog_enabled": True, "prompt": None}
    chat_settings[chat_id]["dialog_enabled"] = False
    save_chat_settings()
    await message.reply("Лана отъебитесь.")

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа говори")
async def enable_dialog(message: types.Message):
    chat_id = str(message.chat.id)
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {"dialog_enabled": True, "prompt": None}
    chat_settings[chat_id]["dialog_enabled"] = True
    save_chat_settings()
    await message.reply("Дратути")

@router.message(F.text.lower() == "промпты")
async def list_prompts_command(message: types.Message):
    await handle_list_prompts_command(message)

@router.message(F.text.lower() == "какой промпт")
async def current_prompt_command(message: types.Message):
    await handle_current_prompt_command(message)

@router.message(F.text.lower().startswith("промпт участник "))
async def set_participant_prompt_command(message: types.Message):
    await handle_set_participant_prompt_command(message)

@router.message(F.text.lower().startswith("промпт "))
async def set_prompt_command(message: types.Message):
    await handle_set_prompt_command(message)

@router.message(F.text.lower() == "поменяй промпт")
async def change_prompt_randomly_command(message: types.Message):
    await handle_change_prompt_randomly_command(message)

@router.message(lambda message: message.text and message.text.lower().startswith(("пирожок", "порошок")))
async def handle_poem(message: types.Message):
    poem_type = "пирожок" if message.text.lower().startswith("пирожок") else "порошок"
    await handle_poem_command(message, poem_type)

@router.message(lambda message: message.text and normalize_upupa_command(message.text).startswith("упупа умоляю"))
async def serious_mode_command(message: types.Message):
    await handle_serious_mode_command(message)

# ================== БЛОК 6.12: ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ ==================
        
