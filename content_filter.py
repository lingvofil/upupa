import json
import os
import asyncio
import re
from typing import Callable, Dict, Any, Awaitable
from collections import defaultdict
from datetime import timedelta

from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, ChatPermissions

from config import ADMIN_ID, ANTISPAM_ENABLED_CHATS

# --- НАСТРОЙКИ ФИЛЬТРА ---
MUTE_DURATION_SECONDS = 60
# STOP_WORDS для защиты от спама
STOP_WORDS = [
    # --- Призывы написать в ЛС (Личные Сообщения) ---
    "в лс", "в личку", "в личке", "в личные сообщения", "в личные сообщение", 
    "личных сообщениях", "пиши в лс", "пишите в лс", "отвечу в лс", "жду в лс", 
    "подробности в лс", "подробности в личке", "пиши сюда", "в директ", "в direct",
    "по всем вопросам в лс", "расскажу подробно", "напиши мне на",

    # --- Общие слова про заработок, работу, доход ---
    "заработок", "заработка", "заработать", "зарабатывай", "зарабатывать", "подработка", 
    "подработку", "доход", "пассивный доход", "дополнительный доход", 
    "работа на дому", "удаленка", "удаленная работа", "работа онлайн", 
    "работа в интернете", "вакансия", "халтура",

    # --- Рекрутинг, набор людей ---
    "требуются", "требуется", "нужны ребята", "нужны люди", "ищу людей", 
    "нужны надежные ответственные люди", "открыт набор", "нужны 2-3 человека",
    "ищу сотрудников", "требуется сотрудник", "набор в команду", "ищем в команду",
    "приглашаю в команду", "срочно требуются", "возьму в команду",

    # --- Оплата и деньги ---
    "оплата", "оплата каждый день", "оплата по факту", "деньги тут", "нужны деньги",
    "быстрые деньги", "легкие деньги", "деньги", "кэш", "бабки", "бабло",

    # --- "Без вложений" и другие заманухи ---
    "без вложений", "вложения не нужны", "места ограничены", "успей", 

    # --- Тематики: Криптовалюты, Трейдинг, Казино ---
    "crypto", "крипта", "криптовалюта", "биток", "биткоин", "нфт", "nft", 
    "бинанс", "binance", "трейдинг", "инвестиции", "вложения",
    "ставки", "букмекер", "слоты", "млм", "mlM",

    # --- Новые проекты, призывы перейти ---
    "новый проект", "переходи по ссылке", "ссылка в профиле", "жми сюда",
    "пиши в тг", "пиши в телеграм", "мой телеграм", "мой тг",

    # --- Искаженные слова (Leetspeak) ---
    "доxod", "дoxoд", "рaбота", "зaработок", "дeньги", # (с латинскими буквами)

    # --- Поиск работы (менее спамное, но может быть нежелательным) ---
    "нужна работа", "ищу работу",
]

# Исключения для ботов (не фильтруются)
ALLOWED_BOTS = ["@expertyebaniebot"]

# Регулярные выражения для продвинутой проверки
SPAM_PATTERNS = [
    re.compile(r"@Amofitlifebot", re.IGNORECASE), # конкретное упоминание бота
    re.compile(r"@\w+bot\b", re.IGNORECASE), # любое упоминание бота (@...bot)
    re.compile(r"(заработок|доход|подработ\w+).{0,20}(\d+\$|\d+\s*доллар|\d+\s*\$)", re.IGNORECASE),
    re.compile(r"(обучени[ея].{0,20}0\s*до\s*результата)", re.IGNORECASE),
    re.compile(r"(в\s*лс|в\s*личн(ые|ку|ые\s*сообщения))", re.IGNORECASE)
]

# --- УПРАВЛЕНИЕ СОСТОЯНИЕМ ФИЛЬТРА ---
ANTISPAM_SETTINGS_FILE = "antispam_enabled.json"

def load_antispam_settings():
    """
    Загружает чаты с включенным антиспамом.
    Этот код уже был написан корректно, используя .update()
    """
    if os.path.exists(ANTISPAM_SETTINGS_FILE):
        try:
            with open(ANTISPAM_SETTINGS_FILE, "r") as f:
                ANTISPAM_ENABLED_CHATS.update(json.load(f))
            print(f"Загружены настройки антиспама. Фильтр включен в {len(ANTISPAM_ENABLED_CHATS)} чатах.")
        except json.JSONDecodeError:
            print(f"Ошибка чтения файла {ANTISPAM_SETTINGS_FILE}. Файл может быть поврежден.")
    else:
        print("Файл настроек антиспама не найден. Фильтр отключен везде по умолчанию.")

def save_antispam_settings():
    with open(ANTISPAM_SETTINGS_FILE, "w") as f:
        json.dump(list(ANTISPAM_ENABLED_CHATS), f)

# Загружаем настройки при старте
load_antispam_settings()

NORMALIZATION_TABLE = str.maketrans("aAeEoOpPcCxX", "аАеЕоОрРсСхХ")

def normalize_text(text: str) -> str:
    return text.lower().translate(NORMALIZATION_TABLE)

def contains_allowed_bot(text: str) -> bool:
    text_lower = text.lower()
    return any(allowed_bot.lower() in text_lower for allowed_bot in ALLOWED_BOTS)

class ContentFilterMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        # Проверяем, включен ли антиспам для чата (ID могут быть и строками в JSON)
        if event.chat.id not in ANTISPAM_ENABLED_CHATS and str(event.chat.id) not in ANTISPAM_ENABLED_CHATS:
            return await handler(event, data)

        if not (event.text or event.caption):
            return await handler(event, data)

        if event.from_user.id == ADMIN_ID:
            return await handler(event, data)

        bot: Bot = data['bot']
        user_id = event.from_user.id
        chat_id = event.chat.id
        now = event.date
        text = event.text or event.caption
        normalized_text = normalize_text(text)

        # Пропускаем команды, начинающиеся с "/"
        if text.strip().startswith("/"):
            return await handler(event, data)

        reason = ""

        # Проверка по стоп-словам
        for stop_word in STOP_WORDS:
            if stop_word in normalized_text:
                reason = "обнаружение спама в сообщении"
                break

        # Проверка по регулярным выражениям
        if not reason:
            for pattern in SPAM_PATTERNS:
                if pattern.search(text):
                    if pattern == SPAM_PATTERNS[1] and contains_allowed_bot(text):
                        continue
                    reason = "обнаружение спама по паттерну"
                    break

        if reason:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=event.message_id)
                mute_duration = timedelta(seconds=MUTE_DURATION_SECONDS)
                await bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=now + mute_duration
                )
            except Exception as e:
                print(f"Не удалось обработать спам от {user_id} в чате {chat_id}: {e}")
            return

        return await handler(event, data)
