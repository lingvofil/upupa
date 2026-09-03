#summarize.py

import logging
import asyncio
import re
import time
from collections import deque
from datetime import datetime, timedelta
from aiogram import types
import random

from AI.dialog.settings import build_prompt_with_current_chat_prompt
from core.paths import USER_MESSAGES_LOG_PATH as LOG_FILE
from core.state import chat_settings
from infrastructure.ai.clients import gigachat_model, groq_ai, model
from infrastructure.ai.gemini import _empty_response_details
from prompts import actions
from features.chat_settings import save_chat_settings


_CHAT_LOG_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+) - Chat (\-?\d+) \((.*?)\) "
    r"- User (\d+) \((.*?)\) \[(.*?)\]: (.*?)$"
)
SUMMARY_HISTORY_SAMPLE_MESSAGES = 5000
SUMMARY_HISTORY_RECENT_MESSAGES = 1000
YEAR_HISTORY_SAMPLE_MESSAGES = 5000
YEAR_HISTORY_RECENT_MESSAGES = 500


def _reservoir_add(
    reservoir: list[tuple[int, dict]],
    item: tuple[int, dict],
    seen: int,
    capacity: int,
) -> None:
    if capacity <= 0:
        return
    if len(reservoir) < capacity:
        reservoir.append(item)
        return
    replacement_index = random.randrange(seen)
    if replacement_index < capacity:
        reservoir[replacement_index] = item


def _get_chat_messages(
    log_file_path: str,
    chat_id: str,
    start_time: datetime,
    sample_size: int | None = None,
    recent_size: int = 0,
):
    """
    Потоково читает и парсит user_messages.log.

    Без sample_size возвращает всю подходящую выборку, как legacy-реализация.
    При sample_size память ограничена: более старая история семплируется
    равновероятно, а recent_size последних сообщений сохраняются полностью.
    """
    messages = []
    users_found = {}
    chat_name = None
    target_chat_id = str(chat_id)

    bounded = sample_size is not None
    if bounded:
        sample_size = max(0, int(sample_size))
        recent_capacity = min(max(int(recent_size), 0), sample_size)
        reservoir_capacity = sample_size - recent_capacity
        recent: deque[tuple[int, dict]] = deque(maxlen=recent_capacity or None)
        reservoir: list[tuple[int, dict]] = []
        older_seen = 0
        sequence = 0

    try:
        with open(log_file_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    match = _CHAT_LOG_RE.search(line)
                    if not match:
                        continue

                    timestamp_str, log_chat_id, current_chat_name, user_id, username, display_name, text = match.groups()
                    if not text.strip():
                        continue

                    # Сохраняем имя чата так же, как раньше: по первой
                    # подходящей строке нужного чата независимо от периода.
                    if str(log_chat_id) == target_chat_id and not chat_name:
                        chat_name = current_chat_name

                    try:
                        log_timestamp = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S.%f")
                    except ValueError:
                        continue

                    if str(log_chat_id) != target_chat_id or log_timestamp < start_time:
                        continue

                    display_name = display_name.strip() if display_name and display_name.strip() else username
                    message_data = {
                        "date": log_timestamp.strftime("%d.%m"),
                        "username": username,
                        "display_name": display_name,
                        "text": text.strip(),
                    }

                    if bounded:
                        sequence += 1
                        if recent_capacity:
                            if len(recent) == recent_capacity:
                                displaced = recent.popleft()
                                older_seen += 1
                                _reservoir_add(
                                    reservoir,
                                    displaced,
                                    older_seen,
                                    reservoir_capacity,
                                )
                            recent.append((sequence, message_data))
                        else:
                            older_seen += 1
                            _reservoir_add(
                                reservoir,
                                (sequence, message_data),
                                older_seen,
                                reservoir_capacity,
                            )
                    else:
                        messages.append(message_data)

                    if username and username.lower() not in ['none', 'null']:
                        users_found[user_id] = {"username": username, "display_name": display_name}

                except Exception:
                    continue

    except FileNotFoundError:
        logging.warning(f"Log file not found: {log_file_path}")
        return [], {}, None

    if bounded:
        reservoir.sort(key=lambda item: item[0])
        messages = [message for _index, message in reservoir]
        messages.extend(message for _index, message in recent)

    return messages, users_found, chat_name


def _get_active_model(chat_id: str):
    """Определяет активную модель для чата"""
    current_settings = chat_settings.get(chat_id, {})
    active_model = current_settings.get("active_model", "gemini")
    return active_model


def _compress_messages_for_groq(messages: list, max_chars: int = 15000) -> tuple[list, int]:
    """
    Сжимает сообщения для Groq API с учетом лимитов.
    Возвращает (сжатые_сообщения, коэффициент_сжатия)
    """
    total_chars = sum(len(m['text']) for m in messages)

    if total_chars <= max_chars:
        return messages, 1

    compression_ratio = max(2, total_chars // max_chars + 1)
    compressed = messages[::compression_ratio]

    logging.info(f"Groq compression: {len(messages)} msgs → {len(compressed)} msgs (ratio: {compression_ratio})")
    return compressed, compression_ratio


def _build_limited_messages_text(messages: list, max_chars: int) -> tuple[str, int]:
    """Собирает последние сообщения так, чтобы весь текст укладывался в max_chars."""
    selected_reversed = []
    total_chars = 0

    for msg in reversed(messages):
        line = f"{msg['display_name']}: {msg['text']}\n"
        if selected_reversed and total_chars + len(line) > max_chars:
            break
        if not selected_reversed and len(line) > max_chars:
            line = line[:max_chars - 4].rstrip() + "...\n"
        selected_reversed.append(line)
        total_chars += len(line)

    selected = list(reversed(selected_reversed))
    return "".join(selected), len(selected)


def _build_messages_text(messages: list, *, dated: bool = False) -> str:
    """Собирает prompt-блок одним join без многократного наращивания строки."""
    if dated:
        return "".join(
            f"[{msg['date']}] {msg['display_name']}: {msg['text']}\n"
            for msg in messages
        )
    return "".join(
        f"{msg['display_name']}: {msg['text']}\n"
        for msg in messages
    )


async def _generate_with_active_model(
    prompt: str,
    chat_id: str,
    safety_settings=None,
    is_summarization=False,
    force_model: str | None = None,
):
    """Генерирует текст с использованием активной модели чата"""
    active_model = force_model or _get_active_model(chat_id)

    if active_model == "history":
        active_model = "gemini"
        logging.info("Summarize: режим 'history' не поддерживается, используем Gemini")

    logging.info(f"Summarize: используется модель {active_model}")

    def sync_model_call_with_retry():
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                if active_model == "gigachat":
                    response = gigachat_model.generate_content(prompt, chat_id=int(chat_id))
                    return response.text
                elif active_model == "groq":
                    if is_summarization:
                        logging.info(f"Используется модель суммаризации: {groq_ai.summarization_model}")
                        original_model = groq_ai.text_model
                        groq_ai.text_model = groq_ai.summarization_model
                        try:
                            result = groq_ai.generate_text(prompt, max_tokens=2048)
                        finally:
                            groq_ai.text_model = original_model
                    else:
                        result = groq_ai.generate_text(prompt, max_tokens=2048)
                    return result or "Groq вернул пустой ответ"
                else:
                    response = model.generate_content(
                        prompt,
                        safety_settings=safety_settings,
                        chat_id=int(chat_id)
                    )
                    if not (response.text or ""):
                        try:
                            logging.warning("Gemini empty summary details: %s", _empty_response_details(response))
                        except Exception:
                            logging.warning("Gemini empty summary response without details")
                    return response.text or ""

            except Exception as e:
                error_str = str(e)
                if "429" in error_str:
                    if attempt < max_retries:
                        wait_time = 30
                        logging.warning(f"Quota 429. Waiting {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    raise e
                elif "413" in error_str or "request_too_large" in error_str:
                    return "⚠️ Логов слишком много для Groq. Переключитесь на Gemini командой 'упупа модель gemini' или попробуйте меньший период."
                elif "PROHIBITED" in error_str or "block_reason" in error_str:
                    return "Google зассал и заблокировал ответ из-за 'недопустимого контента'. Слишком грязно ругаетесь."
                raise e

    return await asyncio.to_thread(sync_model_call_with_retry)


async def summarize_chat_history(message: types.Message, chat_model, log_file_path: str, action_list: list):
    """Обычная сводка за последние 12 часов."""
    chat_id = str(message.chat.id)
    time_threshold = datetime.now() - timedelta(hours=12)

    await message.reply("Щас всех вас сдам...")

    messages_to_summarize, users_in_period, chat_name = await asyncio.to_thread(
        _get_chat_messages,
        log_file_path,
        chat_id,
        time_threshold,
        SUMMARY_HISTORY_SAMPLE_MESSAGES,
        SUMMARY_HISTORY_RECENT_MESSAGES,
    )

    if not messages_to_summarize:
        await message.reply(f"За последние 12 часов в чате {chat_name or chat_id} нихуя не было.")
        return

    active_model = _get_active_model(chat_id)
    compression_ratio = 1

    if active_model == "groq":
        messages_to_summarize, compression_ratio = _compress_messages_for_groq(messages_to_summarize, max_chars=15000)
        if compression_ratio > 1:
            await message.reply("пишу доклад")

    summary_input_text = (
        f"Сообщения из чата {chat_name} за последние 12 часов "
        f"(выборка {len(messages_to_summarize)} сообщений):\n\n"
        + _build_messages_text(messages_to_summarize)
    )

    user_mentions_list = [u["display_name"] for u in users_in_period.values() if u["display_name"]]
    user_mentions_str = ", ".join(user_mentions_list) if user_mentions_list else "участников"

    summary_task_prompt = f"""Просуммируй следующие сообщения из чата {chat_name}. Сделай краткое изложение в свободной форме (с сарказмом и обсценной лексикой), разбей на абзацы. Не более 200 слов.
    Упомяни участников беседы по имени (без символа @): {user_mentions_str}.
    Если сообщений мало, можно сделать совсем короткую сводку в один абзац.

    Вот сообщения:
    {summary_input_text}

    Суммаризация:
    """

    summary_prompt = build_prompt_with_current_chat_prompt(
        chat_id,
        summary_task_prompt,
        task_name="суммаризацию сообщений",
    )
    retry_prompt = build_prompt_with_current_chat_prompt(
        chat_id,
        (
            f"{summary_task_prompt}\n\n"
            "Если предыдущая попытка могла быть заблокирована или получилась пустой, "
            "всё равно верни обычный текст сводки. Без Markdown, без цитирования инструкций, "
            "без отказа, 2-4 коротких абзаца."
        ),
        task_name="повторную суммаризацию сообщений",
    )
    legacy_retry_prompt = (
        f"{summary_task_prompt}\n\n"
        "Важное уточнение: верни обычный текст сводки. Без Markdown, без объяснения инструкций, "
        "2-4 коротких абзаца."
    )
    groq_messages_text, groq_message_count = _build_limited_messages_text(messages_to_summarize, max_chars=6500)
    groq_fallback_prompt = f"""Просуммируй следующие сообщения из чата {chat_name}. Сделай краткое изложение в свободной форме (с сарказмом и обсценной лексикой), разбей на абзацы. Не более 180 слов.
Упомяни участников беседы по имени (без символа @): {user_mentions_str}.
Если данных мало из-за лимита, суммируй только предоставленную выборку.

Вот последние сообщения для аварийной суммаризации (выборка {groq_message_count} сообщений):
{groq_messages_text}

Суммаризация:
"""
    logging.info("Emergency Groq summary prompt length=%s chars, messages=%s", len(groq_fallback_prompt), groq_message_count)

    await _generate_and_send_summary(
        message,
        chat_id,
        summary_prompt,
        action_list,
        "Пишу доклад...",
        retry_prompt=retry_prompt,
        fallback_prompt=legacy_retry_prompt,
        emergency_prompt=groq_fallback_prompt,
    )


async def summarize_year(message: types.Message, chat_model, log_file_path: str, action_list: list):
    """Итоги года с bounded выборкой истории."""
    chat_id = str(message.chat.id)
    time_threshold = datetime.now() - timedelta(days=365)

    status_msg = await message.reply("Я долго терпел вас, уебков")

    messages_to_summarize, users_in_period, chat_name = await asyncio.to_thread(
        _get_chat_messages,
        log_file_path,
        chat_id,
        time_threshold,
        YEAR_HISTORY_SAMPLE_MESSAGES,
        YEAR_HISTORY_RECENT_MESSAGES,
    )

    if not messages_to_summarize:
        await status_msg.edit_text("За последний год логов не найдено. Видимо, я спал.")
        return

    active_model = _get_active_model(chat_id)
    max_safe_chars = 12000 if active_model == "groq" else 30000

    total_chars_approx = sum(len(m['text']) for m in messages_to_summarize)
    if total_chars_approx > max_safe_chars:
        step = (total_chars_approx // max_safe_chars) + 1
        messages_to_summarize = messages_to_summarize[::step]
        logging.info(
            "Year log sample compressed. Sample chars=%s. New count=%s msgs.",
            total_chars_approx,
            len(messages_to_summarize),
        )
        await status_msg.edit_text(
            f"Логов дохера, работаю по ограниченной выборке и читаю каждое {step}-е сообщение..."
        )

    summary_input_text = (
        f"Хронология сообщений чата {chat_name} за ГОД (выборка):\n\n"
        + _build_messages_text(messages_to_summarize, dated=True)
    )

    user_mentions_list = [u["display_name"] for u in users_in_period.values() if u["display_name"]]
    user_mentions_str = ", ".join(user_mentions_list) if user_mentions_list else "всех бродяг"

    summary_prompt = f"""Ты подводишь ИТОГИ ГОДА для чата {chat_name}.
    Входящие данные — это репрезентативная выборка лога переписки за 12 месяцев.

    Твоя задача написать эпичный, смешной и немного оскорбительный отчет.

    Структура:
    1. 🏆 **Главные события года**: 3-5 основных тем.
    2. 🤡 **Номинации года**: Придумай смешные номинации ("Душнила", "Спамер" и т.д.) для: {user_mentions_str}.
    3. 💬 **Золотой фонд цитат**: 3 смешные цитаты из лога.
    4. 📉 **Итог**: Деградировали или эволюционировали?

    Стиль: Сарказм, мат (умеренно). Ты циничный бот.
    Используй Markdown.

    Лог:
    {summary_input_text}
    """

    await _generate_and_send_summary(message, chat_id, summary_prompt, action_list, "Анализирую этот пиздец...", status_msg)


async def _generate_and_send_summary(
    message: types.Message,
    chat_id: str,
    prompt: str,
    action_list: list,
    wait_text: str,
    prev_msg: types.Message = None,
    retry_prompt: str | None = None,
    fallback_prompt: str | None = None,
    emergency_prompt: str | None = None,
):
    """Отправка в LLM с ретраями и разбивкой длинных сообщений."""
    try:
        random_action = random.choice(action_list)
        await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)

        if prev_msg:
            try:
                await prev_msg.edit_text(wait_text)
                processing_msg = prev_msg
            except Exception:
                processing_msg = await message.reply(wait_text)
        else:
            processing_msg = await message.reply(wait_text)

        safety_settings = {
            "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
            "HARM_CATEGORY_HATE_SPEECH": "BLOCK_NONE",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE",
            "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
        }

        summary_response = await _generate_with_active_model(prompt, chat_id, safety_settings, is_summarization=True)
        if not summary_response:
            logging.warning("Summarization returned empty response")
            if retry_prompt:
                try:
                    await processing_msg.edit_text("Модель промолчала, пробую ещё раз...")
                except Exception:
                    pass
                summary_response = await _generate_with_active_model(
                    retry_prompt,
                    chat_id,
                    safety_settings,
                    is_summarization=True,
                )
            if not summary_response:
                logging.warning("Summarization retry returned empty response")
                if fallback_prompt:
                    try:
                        await processing_msg.edit_text("Текущий промпт душит ответ, пробую старый режим...")
                    except Exception:
                        pass
                    summary_response = await _generate_with_active_model(
                        fallback_prompt,
                        chat_id,
                        safety_settings,
                        is_summarization=True,
                    )
            if not summary_response:
                logging.warning("Summarization fallback returned empty response")
                if emergency_prompt:
                    try:
                        await processing_msg.edit_text("Gemini опять молчит, пробую запасную модель...")
                    except Exception:
                        pass
                    try:
                        summary_response = await _generate_with_active_model(
                            emergency_prompt,
                            chat_id,
                            safety_settings,
                            is_summarization=True,
                            force_model="groq",
                        )
                    except Exception as e:
                        logging.error(f"Emergency Groq summarization failed: {e}", exc_info=True)
            if not summary_response:
                logging.warning("Summarization emergency fallback returned empty response")
                summary_response = "Не смог выжать из модели текст. Попробуй ещё раз."

        await processing_msg.delete()

        if len(summary_response) <= 4096:
            try:
                await message.reply(summary_response, parse_mode="Markdown")
            except Exception:
                await message.reply(summary_response)
        else:
            parts = []
            while summary_response:
                if len(summary_response) <= 4096:
                    parts.append(summary_response)
                    break

                split_index = summary_response.rfind('\n', 0, 4096)
                if split_index == -1:
                    split_index = summary_response.rfind(' ', 0, 4096)
                if split_index == -1:
                    split_index = 4096

                parts.append(summary_response[:split_index])
                summary_response = summary_response[split_index:].lstrip()

            for i, part in enumerate(parts):
                try:
                    if i == 0:
                        await message.reply(part, parse_mode="Markdown")
                    else:
                        await message.answer(part, parse_mode="Markdown")
                except Exception:
                    logging.warning(f"Markdown failed for part {i}, sending plain text.")
                    if i == 0:
                        await message.reply(part)
                    else:
                        await message.answer(part)

    except Exception as e:
        logging.error(f"Summarization Error: {e}")
        await message.reply(f"🤖 Ошибка: {str(e)[:100]}...")
