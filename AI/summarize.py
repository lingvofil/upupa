import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, List, Tuple
from zoneinfo import ZoneInfo

from aiogram import types

from core.history_store import get_history_repository
from core.summary_commands import ALL_SUMMARY_COMMANDS
from core.state import chat_settings
from infrastructure.ai.clients import gigachat_model, groq_ai, model
from infrastructure.ai.gemini import _empty_response_details


SUMMARY_HOURS = 12
SUMMARY_SAMPLE_LIMIT = 5000
SUMMARY_RECENT_TAIL = 1000
SUMMARY_TIMEZONE = ZoneInfo("Europe/Moscow")
SUMMARY_COMMANDS_NORMALIZED = {command.lower() for command in ALL_SUMMARY_COMMANDS}
_summary_locks: dict[tuple[int, int], asyncio.Lock] = {}


def _get_active_model(chat_id: str) -> str:
    return chat_settings.get(str(chat_id), {}).get("active_model", "gemini")


def _get_summary_lock(chat_id: int, user_id: int) -> asyncio.Lock:
    key = (int(chat_id), int(user_id))
    lock = _summary_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _summary_locks[key] = lock
    return lock


def _normalize_message_text(text: str | None) -> str:
    return " ".join((text or "").strip().lower().split())


def _is_summary_command(text: str | None) -> bool:
    normalized = _normalize_message_text(text)
    if normalized.startswith("упупа "):
        normalized = normalized[6:].strip()
    return normalized in SUMMARY_COMMANDS_NORMALIZED


def _history_message_to_dict(row) -> dict[str, Any]:
    timestamp = row.timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=SUMMARY_TIMEZONE)
    return {
        "id": row.id,
        "date": timestamp.astimezone(SUMMARY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
        "display_name": row.display_name,
        "text": row.text,
        "message_id": row.message_id,
    }


def _get_chat_messages(
    log_file_path: str,
    chat_id: str | int,
    since: datetime,
    *,
    after_id: int | None = None,
    through_id: int | None = None,
    limit: int | None = None,
) -> Tuple[List[dict], dict, str]:
    """Read chat history from the configured repository, keeping the old file fallback."""
    repository = get_history_repository(log_file_path)
    if repository is not None:
        rows = repository.fetch_messages(
            chat_id,
            since=since,
            after_id=after_id,
            through_id=through_id,
            limit=limit,
        )
        messages = [
            _history_message_to_dict(row)
            for row in rows
            if not _is_summary_command(row.text)
        ]
        return messages, {}, str(chat_id)

    messages: List[dict] = []
    users: dict = {}
    chat_name = str(chat_id)
    pattern = re.compile(
        r"^\[(?P<date>[^]]+)]\s*чат=(?P<chat>-?\d+)\s*"
        r"(?:пользователь_id=(?P<user>-?\d+)\s*)?"
        r"(?:имя=(?P<name>.*?)\s*)?текст=(?P<text>.*)$"
    )
    try:
        with open(log_file_path, "r", encoding="utf-8") as file:
            for line in file:
                match = pattern.match(line.rstrip("\n"))
                if not match or str(match.group("chat")) != str(chat_id):
                    continue
                try:
                    dt = datetime.strptime(match.group("date"), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if dt < since.replace(tzinfo=None):
                    continue
                text = match.group("text") or ""
                if _is_summary_command(text):
                    continue
                messages.append(
                    {
                        "date": match.group("date"),
                        "display_name": (match.group("name") or "неизвестно").strip(),
                        "text": text,
                    }
                )
    except FileNotFoundError:
        pass
    if limit and len(messages) > limit:
        messages = messages[-limit:]
    return messages, users, chat_name


def _sample_messages(messages: List[dict]) -> tuple[List[dict], bool]:
    if len(messages) <= SUMMARY_SAMPLE_LIMIT:
        return messages, False
    recent = messages[-SUMMARY_RECENT_TAIL:]
    older = messages[:-SUMMARY_RECENT_TAIL]
    slots = SUMMARY_SAMPLE_LIMIT - len(recent)
    if slots <= 0:
        return recent[-SUMMARY_SAMPLE_LIMIT:], True
    step = max(1, len(older) // slots)
    sampled = older[::step]
    if len(sampled) > slots:
        sampled = sampled[-slots:]
    return sampled + recent, True


def _trim_messages_to_chars(messages: List[dict], max_chars: int) -> tuple[str, int]:
    if not messages:
        return "", 0
    selected_reversed: list[str] = []
    total_chars = 0
    for msg in reversed(messages):
        line = f"{msg['display_name']}: {msg['text']}\n"
        if selected_reversed and total_chars + len(line) > max_chars:
            break
        if not selected_reversed and len(line) > max_chars:
            line = line[-max_chars:]
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
                        selected_model = groq_ai.summarization_model
                        logging.info(f"Используется модель суммаризации: {selected_model}")
                        result = groq_ai.generate_text(
                            prompt,
                            max_tokens=2048,
                            model=selected_model,
                        )
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


async def summarize_chat_history(message: types.Message, chat_model, log_file_path: str,
                                 action_list: list, *, catchup=False):
    """Fixed 12-hour and personal catch-up modes share the exact same prompt."""
    user = getattr(message, "from_user", None)
    user_id = user.id if user and not getattr(message, "sender_chat", None) else None
    if catchup and user_id is None:
        await message.reply("Для анонимного отправителя персональная отметка сводки недоступна. Используй «чобыло».")
        return

    chat_id = int(message.chat.id)
    lock = _get_summary_lock(chat_id, int(user_id)) if catchup else None
    if lock is not None:
        await lock.acquire()

    try:
        repository = get_history_repository(log_file_path)
        now = datetime.now(SUMMARY_TIMEZONE)
        through_id = None
        cursor = None
        if repository is not None:
            through_id = await asyncio.to_thread(repository.max_message_id, chat_id)
            if through_id is None:
                await message.reply("Новых сообщений для сводки нет.")
                return
            if catchup:
                cursor = await asyncio.to_thread(repository.get_summary_cursor, chat_id, int(user_id))

        if catchup and cursor is not None:
            since = datetime.fromtimestamp(0, SUMMARY_TIMEZONE)
            after_id = cursor.through_id
        else:
            since = now - timedelta(hours=SUMMARY_HOURS)
            after_id = None

        messages, _users, _chat_name = await asyncio.to_thread(
            _get_chat_messages,
            log_file_path,
            chat_id,
            since,
            after_id=after_id,
            through_id=through_id,
        )
        if not messages:
            await message.reply("Новых сообщений для сводки нет.")
            if catchup and repository is not None and through_id is not None:
                await asyncio.to_thread(
                    repository.confirm_summary_cursor,
                    chat_id,
                    int(user_id),
                    through_id,
                    now,
                )
            return

        sampled_messages, sampled = _sample_messages(messages)
        messages_text = _build_messages_text(sampled_messages)
        if not messages_text.strip():
            await message.reply("Новых сообщений для сводки нет.")
            return

        prompt = (
            "Ты читаешь историю сообщений группового чата. Сделай содержательную, но не занудную сводку на русском языке. "
            "Расскажи, что обсуждали, к каким выводам пришли, какие были заметные события, шутки, конфликты или договорённости. "
            "Не выдумывай фактов и не приписывай людям того, чего нет в сообщениях. Не начинай с канцелярского заголовка. "
            "Можно писать живо и с лёгкой иронией.\n\n"
            f"Сообщения:\n{messages_text}"
        )

        if sampled:
            await message.reply(
                f"Сообщений слишком много: в сводку попала выборка {len(sampled_messages)} из {len(messages)} сообщений."
            )

        response = await _generate_with_active_model(
            prompt,
            str(chat_id),
            is_summarization=True,
        )
        if not response:
            await message.reply("Не получилось собрать сводку.")
            return

        chunks = [response[index:index + 4000] for index in range(0, len(response), 4000)]
        for chunk in chunks:
            await message.reply(chunk)

        if catchup and repository is not None and through_id is not None:
            await asyncio.to_thread(
                repository.confirm_summary_cursor,
                chat_id,
                int(user_id),
                through_id,
                now,
            )
    finally:
        if lock is not None and lock.locked():
            lock.release()
