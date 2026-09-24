"""Хэндлеры: Статистика и лексикон.

Вырезано из main.py (этап 3). Порядок регистрации сохранён —
см. handlers/__init__.py: порядок ROUTERS = порядок в старом main.py.
"""
from aiogram import Router

from html import escape
import random
import logging
from aiogram import F, types
from aiogram.types import Message
from typing import Dict
from core.settings import ADMIN_ID
from prompts import actions
from features.stat_rank_settings import get_user_statistics, generate_chat_stats_report
from features.lexicon_settings import (
    process_my_lexicon, process_chat_lexicon, process_user_lexicon
)
import features.statistics as bot_statistics

router = Router(name="stats_lexicon")


TOKEN_USAGE_PERIODS = {
    "токены": (24, "Расход токенов за 24 часа"),
    "токены сутки": (24, "Расход токенов за 24 часа"),
    "токены час": (1, "Расход токенов за час"),
    "токены неделя": (24 * 7, "Расход токенов за неделю"),
    "токены все": (None, "Расход токенов за всё время"),
}


def _format_token_count(value: int) -> str:
    value = int(value or 0)
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f} млрд"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} млн"
    if value >= 1_000:
        return f"{value / 1_000:.1f} тыс."
    return str(value)


def _format_usage_identity(name, username, fallback: str) -> str:
    if username:
        return f"{name} (@{username})" if name else f"@{username}"
    return str(name or fallback)


def format_model_usage_message(report: dict, title: str) -> str:
    totals = report.get("totals", {})
    requests = int(totals.get("requests", 0))
    known = int(totals.get("usage_known_requests", 0))
    total_tokens = int(totals.get("total_tokens", 0))
    input_tokens = int(totals.get("input_tokens", 0))
    output_tokens = int(totals.get("output_tokens", 0))
    cached_tokens = int(totals.get("cached_tokens", 0))
    reasoning_tokens = int(totals.get("reasoning_tokens", 0))
    successful = int(totals.get("successful_requests", 0))
    failed = int(totals.get("failed_requests", 0))
    unknown_outcome = int(totals.get("unknown_outcome_requests", 0))
    unattributed_chats = int(
        totals.get("unattributed_chat_requests", totals.get("unattributed_requests", 0))
    )
    unattributed_users = int(totals.get("unattributed_user_requests", 0))
    interactive = int(totals.get("interactive_requests", 0))
    background = int(totals.get("background_requests", 0))
    telemetry_started_at = totals.get("telemetry_started_at")

    parts = [
        f"🧠 <b>{escape(title)}</b>",
        (
            f"Всего: <b>{_format_token_count(total_tokens)}</b> токенов "
            f"· {requests} запросов"
        ),
        (
            f"Вход: {_format_token_count(input_tokens)} "
            f"· выход: {_format_token_count(output_tokens)}"
        ),
        f"Успешно: {successful} · ошибок: {failed}"
        + (f" · исход неизвестен: {unknown_outcome}" if unknown_outcome else ""),
    ]
    if interactive or background:
        parts.append(f"Вызовы: интерактивные {interactive} · фоновые {background}")
    if telemetry_started_at:
        parts.append(
            "ℹ️ Детальная токен-телеметрия собирается с "
            f"<code>{escape(str(telemetry_started_at))} UTC</code>; "
            "более ранние вызовы в выбранный период восстановить нельзя."
        )
    if cached_tokens or reasoning_tokens:
        extra = []
        if cached_tokens:
            extra.append(f"кэш: {_format_token_count(cached_tokens)}")
        if reasoning_tokens:
            extra.append(f"reasoning: {_format_token_count(reasoning_tokens)}")
        parts.append(" · ".join(extra))

    unknown_usage = max(0, requests - known)
    if unknown_usage:
        parts.append(
            f"⚠️ Без token usage от провайдера: {unknown_usage} запросов"
        )
    if unattributed_chats:
        parts.append(
            f"🛠 Без привязки к чату: {unattributed_chats} запросов"
        )
    if unattributed_users:
        parts.append(
            f"👤 Без привязки к пользователю: {unattributed_users} запросов"
        )

    models = report.get("models") or []
    if models:
        parts.append("\n<b>Модели</b>")
        for row in models:
            label = f"{row.get('provider')}/{row.get('model_name')}"
            parts.append(
                f"• <code>{escape(label)}</code>: "
                f"<b>{_format_token_count(row.get('total_tokens', 0))}</b> "
                f"· {int(row.get('requests', 0))} запр."
            )

    chats = report.get("chats") or []
    if chats:
        parts.append("\n<b>Топ чатов</b>")
        for row in chats:
            label = row.get("chat_title") or f"ID {row.get('chat_id')}"
            parts.append(
                f"• {escape(str(label))}: "
                f"<b>{_format_token_count(row.get('total_tokens', 0))}</b> "
                f"· {int(row.get('requests', 0))} запр."
            )

    users = report.get("users") or []
    if users:
        parts.append("\n<b>Топ пользователей по токенам</b>")
        for row in users:
            label = _format_usage_identity(
                row.get("user_name"),
                row.get("user_username"),
                f"ID {row.get('user_id')}",
            )
            parts.append(
                f"• {escape(label)}: "
                f"<b>{_format_token_count(row.get('total_tokens', 0))}</b> "
                f"· {int(row.get('requests', 0))} запр."
            )

    frequent_users = report.get("users_by_requests") or []
    if frequent_users:
        parts.append("\n<b>Топ пользователей по числу запросов</b>")
        for row in frequent_users:
            label = _format_usage_identity(
                row.get("user_name"),
                row.get("user_username"),
                f"ID {row.get('user_id')}",
            )
            parts.append(
                f"• {escape(label)}: "
                f"<b>{int(row.get('requests', 0))}</b> запр. "
                f"· {_format_token_count(row.get('total_tokens', 0))} токенов"
            )

    return "\n".join(parts)


def format_stats_message(stats: Dict[str, Dict], title: str) -> str:
    parts = [f"📊 *{title}*"]
    if stats.get("model_usage"):
        parts.append("\n🤖 *НАГРУЗКА НА GEMINI (Запросы):*")
        sorted_usage = sorted(stats["model_usage"].items(), key=lambda item: item[1], reverse=True)
        for chat_name, count in sorted_usage:
            parts.append(f"   🔥 `{chat_name}`: {count} запросов")
    else:
        parts.append("\n_Запросов к Gemini не зафиксировано._")

    if stats.get("groups"):
        parts.append("\n*Активность (Сообщения в чатах):*")
        sorted_groups = sorted(stats["groups"].items(), key=lambda item: item[1], reverse=True)
        for chat_title, count in sorted_groups:
            parts.append(f"   • `{chat_title}`: {count} сообщ.")
    else:
        parts.append("\n_Нет активности в групповых чатах._")

    if stats.get("private"):
        parts.append("\n*Личные сообщения:*")
        sorted_private = sorted(stats["private"].items(), key=lambda item: item[1], reverse=True)
        for user_display, count in sorted_private:
            parts.append(f"   • `{user_display}`: {count} сообщ.")
    else:
        parts.append("\n_Нет активности в личных сообщениях._")

    return "\n".join(parts)

@router.message(
    lambda message: (
        message.from_user
        and message.from_user.id == ADMIN_ID
        and message.text
        and message.text.lower().strip() in TOKEN_USAGE_PERIODS
    )
)
async def cmd_token_usage(message: Message):
    period_hours, title = TOKEN_USAGE_PERIODS[message.text.lower().strip()]
    report = await bot_statistics.get_model_usage_report(period_hours, limit=7)
    await message.bot.send_message(
        chat_id=ADMIN_ID,
        text=format_model_usage_message(report, title),
        parse_mode="HTML",
    )


@router.message(F.text.lower() == "стотистика", F.from_user.id == ADMIN_ID)
async def cmd_stats_total(message: Message):
    stats_data = await bot_statistics.get_total_messages()
    reply_text = format_stats_message(stats_data, "Общая статистика")
    await message.answer(reply_text, parse_mode="Markdown")

@router.message(F.text.lower() == "стотистика сутки", F.from_user.id == ADMIN_ID)
async def cmd_stats_24h(message: Message):
    stats_data = await bot_statistics.get_messages_last_24_hours()
    reply_text = format_stats_message(stats_data, "Статистика за 24 часа")
    await message.answer(reply_text, parse_mode="Markdown")

@router.message(F.text.lower() == "стотистика час", F.from_user.id == ADMIN_ID)
async def cmd_stats_1h(message: Message):
    stats_data = await bot_statistics.get_messages_last_hour()
    reply_text = format_stats_message(stats_data, "Статистика за час")
    await message.answer(reply_text, parse_mode="Markdown")

@router.message(F.text.lower() == "моя статистика")
async def show_personal_stats(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    logging.info(f"Команда 'моя статистика' вызвана пользователем {message.from_user.id} в чате {message.chat.id}")
    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id)
    response, has_stats = await get_user_statistics(chat_id, user_id)
    await message.reply(response)

@router.message(F.text.lower() == "статистика чат")
async def show_chat_stats(message: types.Message):
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    report = await generate_chat_stats_report(str(message.chat.id))
    reply_text = report if report else "В этом чате нет корректных статистических данных."
    await message.reply(reply_text, parse_mode="HTML")

@router.message(lambda message: message.text and message.text.lower() == "мой лексикон")
async def handle_my_lexicon(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    user_id = message.from_user.id
    chat_id = message.chat.id
    await process_my_lexicon(user_id, chat_id, message)

@router.message(lambda message: message.text and message.text.lower() == "лексикон чат")
async def handle_chat_lexicon(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    response_text = await process_chat_lexicon(message)
    await message.reply(response_text)
    
@router.message(lambda message: message.text and message.text.lower().startswith("лексикон "))
async def handle_user_lexicon(message: types.Message):
    random_action = random.choice(actions)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=random_action)
    username_or_name = message.text[len("лексикон "):].strip()
    if username_or_name.startswith('@'):
        username_or_name = username_or_name[1:]       
    chat_id = message.chat.id
    await process_user_lexicon(username_or_name, chat_id, message)

# ================== БЛОК 5.6: ПОИСК И МЕДИА ==================
