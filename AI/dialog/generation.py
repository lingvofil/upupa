"""Dialogue generation, conversation history and context enrichment."""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from aiogram import types
from aiogram.enums import ContentType

from core.state import MAX_HISTORY_LENGTH, chat_settings, conversation_history
from infrastructure.ai.clients import (
    gigachat_model,
    groq_ai,
    model,
    openrouter_ai,
    siliconflow_ai,
)
from core.history_engine import load_and_find_answer
from core.upupa_utils import normalize_upupa_command
from features.lexicon_settings import extract_messages_by_full_name, extract_messages_by_username
from prompts import DIALOG_TRIGGER_KEYWORDS
from services.smart_search import find_relevant_context
from services.web_context import get_web_context, needs_web_search

from AI.response_sanitizer import strip_confidence_percentages
from AI.dialog.settings import (
    NO_CONFIDENCE_PERCENTAGES_INSTRUCTION,
    get_current_chat_prompt,
    update_chat_settings,
)


GenerateResponseCallable = Callable[[str, str, str, str], Awaitable[str]]
NeedsWebSearchCallable = Callable[[str], bool]
GetWebContextCallable = Callable[[str], Awaitable[str]]


def get_error_reply_text() -> str:
    """Return the legacy random error reply."""
    if random.random() < 0.5:
        return "ошибка блят"
    return "Произошла ошибка при обработке."


async def generate_simple_response(prompt: str, chat_id: str) -> str:
    """Generate a standalone response without dialogue history."""
    try:
        update_chat_settings(chat_id)
        current_settings = chat_settings.get(chat_id, {})
        active_model = current_settings.get("active_model", "gemini")

        if active_model == "history":
            active_model = "gemini"

        logging.info("generate_simple_response: используется модель %s", active_model)
        logging.info("generate_simple_response: промпт = %s...", prompt[:200])

        def sync_model_call():
            if active_model == "gigachat":
                response = gigachat_model.generate_content(prompt, chat_id=int(chat_id))
                return response.text
            if active_model == "groq":
                result = groq_ai.generate_text(prompt)
                logging.info("Groq вернул: %r", result)
                return result
            if active_model == "openrouter":
                result = openrouter_ai.generate_text(prompt)
                logging.info("OpenRouter вернул: %r", result[:100] if result else "")
                return result
            if active_model == "siliconflow":
                result = siliconflow_ai.generate_text(prompt)
                logging.info("SiliconFlow вернул: %r", result[:100] if result else "")
                return result

            response = model.generate_content(prompt, chat_id=int(chat_id))
            return response.text

        response_text = await asyncio.to_thread(sync_model_call)
        if response_text is None:
            logging.warning("generate_simple_response: модель вернула None")
            response_text = ""

        logging.info("generate_simple_response: получен ответ длиной %s символов", len(response_text))
        logging.info("generate_simple_response: ответ = %r", response_text[:200])

        response_text = strip_confidence_percentages(response_text)
        if not response_text.strip():
            logging.warning("generate_simple_response: ответ пустой!")
            response_text = "Я пока не знаю, что ответить... 😅"

        return response_text[:4000]
    except Exception as exc:
        logging.error("Model API Error in generate_simple_response: %s", exc, exc_info=True)
        return get_error_reply_text()


def update_conversation_history(chat_id: str, name: str, message_text: str, role: str) -> None:
    if chat_id not in conversation_history:
        conversation_history[chat_id] = []
    conversation_history[chat_id].append({"role": role, "name": name, "content": message_text})
    if len(conversation_history[chat_id]) > MAX_HISTORY_LENGTH:
        conversation_history[chat_id] = conversation_history[chat_id][-MAX_HISTORY_LENGTH:]


def format_chat_history(chat_id: str) -> str:
    if chat_id not in conversation_history or not conversation_history[chat_id]:
        return "Диалог только начинается."
    return "\n".join(f"{msg['name']}: {msg['content']}" for msg in conversation_history[chat_id])


async def generate_response(prompt: str, chat_id: str, bot_name: str, user_input: str = "") -> str:
    """Generate a dialogue response through the chat's active model."""
    try:
        update_chat_settings(chat_id)
        current_settings = chat_settings.get(chat_id, {})
        active_model = current_settings.get("active_model", "gemini")

        if active_model == "history":
            ans = await asyncio.to_thread(load_and_find_answer, user_input, chat_id, 3)
            if ans:
                update_conversation_history(chat_id, bot_name, ans, role="assistant")
                return ans
            return "Отъебись"

        prompt_type = current_settings.get("prompt_type", "standard")
        generation_kwargs = {}
        if prompt_type == "user_style":
            generation_kwargs = {
                "temperature": 0.9,
                "presence_penalty": 0.6,
            }

        def sync_model_call():
            if active_model == "gigachat":
                response = gigachat_model.generate_content(
                    prompt,
                    chat_id=int(chat_id),
                    temperature=generation_kwargs.get("temperature", 0.7),
                )
                return response.text
            if active_model == "groq":
                return groq_ai.generate_text(prompt, **generation_kwargs)
            if active_model == "openrouter":
                result = openrouter_ai.generate_text(prompt, **generation_kwargs)
                logging.info("OpenRouter вернул: %r", result[:100] if result else "")
                return result
            if active_model == "siliconflow":
                result = siliconflow_ai.generate_text(prompt, **generation_kwargs)
                logging.info("SiliconFlow вернул: %r", result[:100] if result else "")
                return result

            gemini_kwargs = {}
            if generation_kwargs:
                gemini_kwargs["generation_config"] = {
                    "temperature": generation_kwargs["temperature"],
                }
            response = model.generate_content(
                prompt,
                chat_id=int(chat_id),
                **gemini_kwargs,
            )
            return response.text

        response_text = await asyncio.to_thread(sync_model_call)
        if response_text is None:
            logging.warning("generate_response: модель вернула None")
            response_text = ""

        response_text = strip_confidence_percentages(response_text)
        if not response_text.strip():
            response_text = "Я пока не знаю, что ответить... 😅"

        update_conversation_history(chat_id, bot_name, response_text, role="assistant")
        return response_text[:4000]
    except Exception as exc:
        logging.error("Model API Error: %s", exc)
        error_message = get_error_reply_text()
        update_conversation_history(chat_id, bot_name, error_message, role="assistant")
        return error_message


async def handle_bot_conversation(
    message: types.Message,
    user_first_name: str,
    *,
    generate_response_func: GenerateResponseCallable | None = None,
    needs_web_search_func: NeedsWebSearchCallable | None = None,
    get_web_context_func: GetWebContextCallable | None = None,
) -> str:
    """Handle one direct dialogue message.

    Optional callables keep the legacy ``AI.talking`` facade testable without
    mutating this module at runtime. Production callers use the defaults.
    """
    chat_id = str(message.chat.id)

    original_user_input = message.text or ""
    user_input = message.text
    if not user_input or not isinstance(user_input, str):
        user_input = ""

    if user_input.lower().startswith("упупа"):
        temp_input_lower = normalize_upupa_command(user_input)
    else:
        temp_input_lower = user_input.lower()

    for keyword in DIALOG_TRIGGER_KEYWORDS:
        if temp_input_lower.startswith(keyword):
            user_input = user_input[len(keyword):].lstrip(" ,")
            break

    if not user_input.strip() and message.from_user and message.from_user.is_bot:
        user_input = original_user_input.strip()
        if not user_input and message.content_type == ContentType.UNKNOWN:
            user_input = "[сообщение другого бота без доступного текста]"

    if not user_input.strip():
        return "Хули?"

    update_conversation_history(chat_id, user_first_name, user_input, role="user")
    selected_prompt, prompt_name = get_current_chat_prompt(chat_id)

    current_settings = chat_settings.get(chat_id, {})
    additional_context = ""
    if current_settings.get("prompt_type") == "user_style":
        imitated_user_data = current_settings.get("imitated_user", {})
        target_name = imitated_user_data.get("username") or imitated_user_data.get("full_name")

        if target_name:
            if imitated_user_data.get("username"):
                messages = await extract_messages_by_username(imitated_user_data["username"], chat_id)
            else:
                messages = await extract_messages_by_full_name(imitated_user_data["full_name"], chat_id)

            if messages:
                relevant_msgs = await find_relevant_context(user_input, messages, top_k=3)
                if relevant_msgs:
                    additional_context = (
                        f"\n\nВАЖНО! Вот что {prompt_name} говорил(а) на похожие темы или в похожем контексте ранее:\n"
                        f"{' | '.join(relevant_msgs)}\n"
                        f"Используй эти фразы или мысли, чтобы ответ был максимально похож на него/неё."
                    )
                    logging.info("Smart Search added context for %s: %s", prompt_name, relevant_msgs)

    should_search = needs_web_search_func or needs_web_search
    fetch_web_context = get_web_context_func or get_web_context
    web_context = ""
    if current_settings.get("active_model", "gemini") != "history" and should_search(user_input):
        try:
            web_context = await fetch_web_context(user_input)
            if web_context:
                logging.info("Web Search added context for: %s", user_input[:80])
        except Exception as exc:
            logging.warning("Web Search failed: %s", exc)

    chat_history_formatted = format_chat_history(chat_id)
    full_prompt = (
        f"{selected_prompt}\n"
        f"{NO_CONFIDENCE_PERCENTAGES_INSTRUCTION}\n"
        f"{additional_context}"
        f"{web_context}\n"
        f"Это текущий диалог в групповом чате. Твоя задача — органично его продолжить от лица '{prompt_name}'.\n"
        f"Вот история диалога:\n{chat_history_formatted}\n"
        f"{prompt_name}:"
    )

    generator = generate_response_func or generate_response
    return await generator(full_prompt, chat_id, prompt_name, user_input=user_input)
