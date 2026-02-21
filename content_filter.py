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
    "binance",
    "bybit",
    "crypto",
    "hurry up",
    "hurry up claim free PUMP",
    "mlM",
    "nft",
    "usd в день",
    "USDT",
    "бeз пpeдoплaты",
    "без вложений",
    "бинанс",
    "букмекер",
    "быстрые деньги",
    "в direct",
    "в директ",
    "в личные сообщение",
    "в личные сообщения",
    "вложения не нужны",
    "возьму в команду",
    "всему научим с нуля",
    "дам работу",
    "деньги тут",
    "дневной доход",
    "доxod",
    "дополнительный доход",
    "достойный доход",
    "доход онлайн",
    "доход от",
    "доход приличный",
    "доход стабильный",
    "жду «+»",
    "жду в лс",
    "жми сюда",
    "за всеми подробностями",
    "за наличные",
    "за простую помощь",
    "залетайте в приватку",
    "зарабатывать хорошие деньги",
    "инвестиции",
    "ищем в команду",
    "ищем профессионалов",
    "ищу людей",
    "ищу несколько человек",
    "ищу одного человека",
    "ищу помощников",
    "ищу помощь по работе",
    "ищу работу",
    "ищу сотрудников",
    "ищу целеустремленных",
    "кому интересно напишите",
    "крипта",
    "криптовалюта",
    "кто хочет зарабатывать",
    "легальный доход",
    "легкие деньги",
    "места ограничены",
    "млм",
    "мой тг",
    "мой телеграм",
    "набираю людей",
    "набор в команду",
    "набор людей",
    "напиши мне на",
    "нужен помощник",
    "нужна помощь по рабочему вопросу",
    "нужна работа",
    "нужно пару человек",
    "нужны 2-3 человека",
    "нужны деньги",
    "нужны люди",
    "нужны надежные ответственные люди",
    "нужны ребята",
    "нфт",
    "обращайтесь в сообщения",
    "оплата каждый день",
    "оплата по факту",
    "оплата сразу",
    "опыт не требуется",
    "отвечу в лс",
    "открыт набор",
    "открыты вакансии",
    "пассивный доход",
    "переходи по ссылке",
    "пиши в личку",
    "пиши в лс",
    "пиши в тг",
    "пиши в телеграм",
    "пиши мне я помогу",
    "пиши скорее сюда",
    "пишите в личку",
    "пишите в лс",
    "пишите мне в лс",
    "пишите напрямую",
    "пишите: +",
    "по всем вопросам в лс",
    "подработка от",
    "подробности в личке",
    "подробности в лс",
    "получай деньги в два счета",
    "помогу нуждающимся",
    "помощь с жильем",
    "помощь с закрытием кредитов",
    "приветствую в нашу команду",
    "приглашаю в команду",
    "р в неделю",
    "работа в интернете",
    "работа на дому",
    "работа онлайн",
    "работа полностью онлайн",
    "работать пару часов",
    "расскажу подробно",
    "расскажу у себя в ЛС",
    "смотри описание профиля",
    "срочно нужен персонал",
    "срочно нужны",
    "срочно требуются",
    "ссылка в профиле",
    "стабильный доход",
    "требуется сотрудник",
    "требуются двое",
    "удаленная работа",
    "удаленного заработка",
    "удаленную деятельность",
    "удаленщика в проект",
    "удалённую деятельность",
    "хороший заработок",
    "часа в день",
    "지갑",
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
