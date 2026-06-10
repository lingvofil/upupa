"""Хэндлеры: Переключение AI-моделей и истории.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import FSInputFile, Message, PollAnswer, BufferedInputFile
from core.upupa_utils import normalize_upupa_command
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

router = Router(name="ai_modes")


@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа гигачат")
async def switch_to_gigachat(message: types.Message):
    await handle_switch_to_gigachat(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа гемини")
async def switch_to_gemini(message: types.Message):
    await handle_switch_to_gemini(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа грок")
async def switch_to_groq(message: types.Message):
    await handle_switch_to_groq(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа нушо")
async def cmd_switch_history(message: Message):
    await handle_switch_to_history(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа опен")
async def switch_to_openrouter(message: types.Message):
    await handle_switch_to_openrouter(message)

@router.message(lambda message: message.text and normalize_upupa_command(message.text) == "упупа силикон")
async def switch_to_siliconflow(message: types.Message):
    await handle_switch_to_siliconflow(message)

@router.message(F.text.lower() == "какая модель")
async def which_model(message: types.Message):
    await handle_which_model(message)

# ================== БЛОК 6.2: ПРОФИЛИ И ПАРОДИЯ ==================
