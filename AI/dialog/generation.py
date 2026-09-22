"""Dialogue generation, conversation history and context enrichment."""

import asyncio
import logging
import random
import re
from difflib import SequenceMatcher
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
from core.settings import MODEL_QUEUE_PLEADING
from core.history_engine import load_and_find_answer
from core.upupa_utils import normalize_upupa_command
from features.chat_settings import save_chat_settings
from prompts import DIALOG_TRIGGER_KEYWORDS
from services.web_context import get_web_context, needs_web_search

from AI.response_sanitizer import strip_confidence_percentages
from AI.dialog.participant_imitation import prepare_participant_turn
from AI.dialog.settings import (
    NO_CONFIDENCE_PERCENTAGES_INSTRUCTION,
    get_current_chat_prompt,
    update_chat_settings,
)


GenerateResponseCallable = Callable[[str, str, str, str], Awaitable[str]]
NeedsWebSearchCallable = Callable[[str], bool]
GetWebContextCallable = Callable[[str], Awaitable[str]]
MAX_REPLY_CONTEXT_LENGTH = 6000
PARTICIPANT_REPEAT_WINDOW = 6
PARTICIPANT_REPEAT_SIMILARITY = 0.78


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


def _normalize_participant_reply(text: str) -> str:
    normalized = re.sub(r"[^\w\s]+", " ", (text or "").casefold(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _recent_participant_replies(
    chat_id: str,
    bot_name: str,
    *,
    limit: int = PARTICIPANT_REPEAT_WINDOW,
) -> list[str]:
    replies: list[str] = []
    for item in reversed(conversation_history.get(chat_id, [])):
        if item.get("role") != "assistant" or item.get("name") != bot_name:
            continue
        content = str(item.get("content") or "").strip()
        if content:
            replies.append(content)
        if len(replies) >= limit:
            break
    replies.reverse()
    return replies


def _participant_reply_is_too_repetitive(response: str, recent_replies: list[str]) -> bool:
    candidate = _normalize_participant_reply(response)
    candidate_tokens = candidate.split()
    if len(candidate_tokens) < 2:
        return False

    for previous in recent_replies:
        old = _normalize_participant_reply(previous)
        old_tokens = old.split()
        if len(old_tokens) < 2:
            continue
        if candidate == old:
            return True

        # Short Telegram replies need a lower threshold: "обезян обезян" and
        # "обезян обезян обезян" are the same conversational move even though
        # one has an extra repeated token.
        threshold = (
            PARTICIPANT_REPEAT_SIMILARITY
            if max(len(candidate), len(old)) <= 120
            else 0.9
        )
        if SequenceMatcher(None, candidate, old).ratio() >= threshold:
            return True

    return False


def _format_participant_repetition_guard(chat_id: str, bot_name: str) -> str:
    recent = _recent_participant_replies(chat_id, bot_name)
    if not recent:
        return ""

    lines = "\n".join(f"- {reply[:240]}" for reply in recent)
    return (
        "\n[ANTI-REPETITION]\n"
        "Ниже твои недавние ответы в этом образе. Сохраняй стиль, но не повторяй ту же фирменную фразу, "
        "шутку или почти тот же ответ в соседних ходах только ради узнаваемости. Выбирай другой характерный "
        "ход, если текущий контекст не требует повтора.\n"
        f"{lines}\n"
        "[/ANTI-REPETITION]\n"
    )


def _format_reply_author(message: types.Message) -> str:
    author = getattr(message, "from_user", None)
    if author:
        return (
            getattr(author, "full_name", None)
            or getattr(author, "first_name", None)
            or getattr(author, "username", None)
            or "неизвестный автор"
        )

    sender_chat = getattr(message, "sender_chat", None)
    if sender_chat:
        return getattr(sender_chat, "title", None) or getattr(sender_chat, "username", None) or "чат"

    return "неизвестный автор"


def _get_attached_bot_id(message: types.Message) -> int | None:
    """Return the Telegram ID of the bot attached to an incoming Message, if available."""
    try:
        attached_bot = message.bot
    except (AttributeError, RuntimeError):
        return None
    return getattr(attached_bot, "id", None)


def _format_poll_reply_context(poll) -> str:
    question = (getattr(poll, "question", None) or "").strip()
    options = list(getattr(poll, "options", None) or [])
    option_texts = [str(getattr(option, "text", "")).strip() for option in options]

    lines = ["Тип сообщения: викторина/опрос."]
    if question:
        lines.append(f"Вопрос: {question}")
    if option_texts:
        lines.append(
            "Варианты: "
            + "; ".join(f"{index}. {text}" for index, text in enumerate(option_texts, 1) if text)
        )

    correct_option_id = getattr(poll, "correct_option_id", None)
    if isinstance(correct_option_id, int) and 0 <= correct_option_id < len(option_texts):
        lines.append(
            f"Правильный вариант: {correct_option_id + 1}. {option_texts[correct_option_id]}"
        )

    explanation = (getattr(poll, "explanation", None) or "").strip()
    if explanation:
        lines.append(f"Пояснение: {explanation}")

    return "\n".join(lines)


def format_reply_context(message: types.Message) -> str:
    """Format the Telegram message being replied to as immediate LLM context."""
    replied = getattr(message, "reply_to_message", None)
    if not replied:
        return ""

    reply_author = getattr(replied, "from_user", None)
    bot_user_id = _get_attached_bot_id(message)
    is_own_bot_message = (
        reply_author is not None
        and bot_user_id is not None
        and getattr(reply_author, "id", None) == bot_user_id
    )

    parts = [f"Автор сообщения: {_format_reply_author(replied)}"]
    if is_own_bot_message:
        parts.append(
            "Важно: это сообщение написал ты сам — текущий Telegram-бот Упупа, "
            "а не пользователь чата и не другой бот."
        )

    quote = getattr(message, "quote", None)
    quote_text = (getattr(quote, "text", None) or "").strip()
    if quote_text:
        if getattr(quote, "is_manual", False):
            parts.append(
                "Пользователь использовал Telegram Quote & Reply и специально выделил "
                f"следующий фрагмент:\n{quote_text}\n"
                "Это главный предмет текущего ответа; полный текст исходного сообщения ниже — фон."
            )
        else:
            parts.append(
                "Telegram передал цитируемый фрагмент ответа:\n"
                f"{quote_text}\n"
                "Считай этот фрагмент главным предметом текущего ответа; полный текст исходного "
                "сообщения ниже — фон."
            )

    text = (getattr(replied, "text", None) or getattr(replied, "caption", None) or "").strip()
    if text:
        label = "Полный текст исходного сообщения (фон)" if quote_text else "Текст сообщения"
        parts.append(f"{label}:\n{text}")

    poll = getattr(replied, "poll", None)
    if poll:
        parts.append(_format_poll_reply_context(poll))

    media_labels = []
    for attr, label in (
        ("photo", "фото"),
        ("video", "видео"),
        ("animation", "анимация/GIF"),
        ("audio", "аудио"),
        ("voice", "голосовое сообщение"),
        ("document", "файл"),
        ("sticker", "стикер"),
    ):
        if getattr(replied, attr, None):
            media_labels.append(label)
    if media_labels:
        parts.append(f"В сообщении также было: {', '.join(media_labels)}.")

    context = "\n".join(parts).strip()
    if len(context) > MAX_REPLY_CONTEXT_LENGTH:
        context = context[:MAX_REPLY_CONTEXT_LENGTH].rstrip() + "…"
    return context


async def generate_response(
    prompt: str,
    chat_id: str,
    bot_name: str,
    user_input: str = "",
    *,
    force_gemini: bool = False,
    gemini_model_queue: list[str] | None = None,
) -> str:
    """Generate a dialogue response through the chat's active model."""
    try:
        update_chat_settings(chat_id)
        current_settings = chat_settings.get(chat_id, {})
        active_model = current_settings.get("active_model", "gemini")
        if force_gemini:
            active_model = "gemini"

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

        def sync_model_call(prompt_text: str):
            if active_model == "gigachat":
                response = gigachat_model.generate_content(
                    prompt_text,
                    chat_id=int(chat_id),
                    temperature=generation_kwargs.get("temperature", 0.7),
                )
                return response.text
            if active_model == "groq":
                return groq_ai.generate_text(prompt_text, **generation_kwargs)
            if active_model == "openrouter":
                result = openrouter_ai.generate_text(prompt_text, **generation_kwargs)
                logging.info("OpenRouter вернул: %r", result[:100] if result else "")
                return result
            if active_model == "siliconflow":
                result = siliconflow_ai.generate_text(prompt_text, **generation_kwargs)
                logging.info("SiliconFlow вернул: %r", result[:100] if result else "")
                return result

            gemini_kwargs = {}
            if generation_kwargs:
                gemini_kwargs["generation_config"] = {
                    "temperature": generation_kwargs["temperature"],
                }
            response = model.generate_content(
                prompt_text,
                chat_id=int(chat_id),
                model_queue=gemini_model_queue,
                **gemini_kwargs,
            )
            return response.text

        recent_participant_replies = (
            _recent_participant_replies(chat_id, bot_name)
            if prompt_type == "user_style"
            else []
        )
        response_text = await asyncio.to_thread(sync_model_call, prompt)

        if (
            prompt_type == "user_style"
            and response_text
            and _participant_reply_is_too_repetitive(response_text, recent_participant_replies)
        ):
            logging.info(
                "Participant imitation repetition detected chat=%s persona=%s; retrying once",
                chat_id,
                bot_name,
            )
            recent_lines = "\n".join(
                f"- {reply[:240]}" for reply in recent_participant_replies
            )
            retry_prompt = (
                f"{prompt}\n\n[ANTI-REPETITION RETRY]\n"
                "Черновик получился слишком похожим на один из недавних ответов. Сохрани манеру этого "
                "участника, но полностью смени конкретную формулировку и разговорный ход. Не используй "
                "ту же фирменную фразу как заполнитель.\n"
                f"Недавние ответы:\n{recent_lines}\n"
                f"Отбракованный черновик: {str(response_text)[:240]}\n"
                "[/ANTI-REPETITION RETRY]\n"
                f"{bot_name}:"
            )
            response_text = await asyncio.to_thread(sync_model_call, retry_prompt)
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


async def generate_pleading_response(
    prompt: str,
    chat_id: str,
    bot_name: str,
    user_input: str = "",
) -> str:
    """Route "упупа умоляю" through Gemini 3.8 with normal Gemini fallbacks."""
    return await generate_response(
        prompt,
        chat_id,
        bot_name,
        user_input=user_input,
        force_gemini=True,
        gemini_model_queue=MODEL_QUEUE_PLEADING,
    )


async def handle_bot_conversation(
    message: types.Message,
    user_first_name: str,
    *,
    generate_response_func: GenerateResponseCallable | None = None,
    needs_web_search_func: NeedsWebSearchCallable | None = None,
    get_web_context_func: GetWebContextCallable | None = None,
) -> str:
    """Handle one direct dialogue message.

    Optional callables keep the dialogue layer testable without mutating this
    module at runtime. Production callers use the defaults.
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

    pleading_trigger = "упупа умоляю"
    is_pleading = (
        temp_input_lower == pleading_trigger
        or temp_input_lower.startswith(pleading_trigger + " ")
    )

    if is_pleading:
        match = re.match(
            r"^\s*упупа(?:[\s,;:!?.—–-]+)умоляю(?:[\s,;:!?.—–-]+)?(.*)$",
            user_input,
            flags=re.IGNORECASE | re.DOTALL,
        )
        user_input = (match.group(1) if match else "").strip()
    else:
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

    current_settings = chat_settings.get(chat_id, {})
    reply_context = format_reply_context(message)
    additional_context = ""
    if current_settings.get("prompt_type") == "user_style":
        try:
            semantic_query = user_input
            if reply_context:
                semantic_query = (
                    f"{user_input}\n\n"
                    f"Контекст сообщения, на которое отвечают:\n{reply_context}"
                )
            additional_context, profile_changed = await prepare_participant_turn(
                chat_id,
                current_settings,
                semantic_query,
                current_message_text=user_input,
            )
            if profile_changed:
                save_chat_settings()
        except Exception as exc:
            logging.warning("Participant imitation context failed: %s", exc, exc_info=True)
            additional_context = (
                "\n\n[SEMANTIC MEMORY]\nПамять участника временно недоступна. "
                "Имитируй только стиль и не выдумывай его реальные взгляды или опыт.\n[/SEMANTIC MEMORY]"
            )

    # Read the selected prompt only after participant preparation: a stale
    # profile may have been automatically rebuilt on this very turn.
    selected_prompt, prompt_name = get_current_chat_prompt(chat_id)

    should_search = needs_web_search_func or needs_web_search
    fetch_web_context = get_web_context_func or get_web_context
    web_context = ""
    uses_history = (
        current_settings.get("active_model", "gemini") == "history"
        and not is_pleading
    )
    if not uses_history and should_search(user_input):
        try:
            web_context = await fetch_web_context(user_input)
            if web_context:
                logging.info("Web Search added context for: %s", user_input[:80])
        except Exception as exc:
            logging.warning("Web Search failed: %s", exc)

    reply_context_block = ""
    if reply_context:
        reply_context_block = (
            "\nНепосредственный контекст реплая:\n"
            f"{reply_context}\n"
            "Пользователь отвечает именно на это сообщение. Если Telegram передал выделенную "
            "цитату, считай прежде всего её главным предметом текущего вопроса, а полный текст "
            "исходного сообщения используй как фон. Если цитаты нет, считай всё сообщение "
            "главным локальным контекстом, даже если общая история чата содержит другие темы.\n"
        )

    chat_history_formatted = format_chat_history(chat_id)
    repetition_guard = (
        _format_participant_repetition_guard(chat_id, prompt_name)
        if current_settings.get("prompt_type") == "user_style"
        else ""
    )
    full_prompt = (
        f"{selected_prompt}\n"
        f"{NO_CONFIDENCE_PERCENTAGES_INSTRUCTION}\n"
        f"{additional_context}"
        f"{repetition_guard}"
        f"{web_context}\n"
        f"{reply_context_block}"
        f"Это текущий диалог в групповом чате. Твоя задача — органично его продолжить от лица '{prompt_name}'.\n"
        f"Вот история диалога:\n{chat_history_formatted}\n"
        f"{prompt_name}:"
    )

    if generate_response_func is not None:
        generator = generate_response_func
    elif is_pleading:
        generator = generate_pleading_response
    else:
        generator = generate_response
    return await generator(full_prompt, chat_id, prompt_name, user_input=user_input)
