import asyncio
import logging
from datetime import datetime
from aiogram import types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from core.paths import USER_MESSAGES_LOG_PATH
from core.settings import ADMIN_ID

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def log_message(message: str):
    """Логирование сообщений рассылки"""
    timestamp = datetime.now().isoformat()
    # Используем 'a' для добавления записи, encoding='utf-8' для корректной работы с кириллицей
    with open(USER_MESSAGES_LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f"{timestamp} - BROADCAST - {message}\n")

async def get_all_chats_from_log():
    """
    Получение уникальных чатов из лог-файла.
    Оставляет ТОЛЬКО ГРУППЫ (ID < 0), исключая личные сообщения.
    """
    chats = set()
    try:
        with open(USER_MESSAGES_LOG_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                if " - Chat " in line:
                    # Извлекаем ID чата из строки лога
                    # Пример строки: ... - Chat -100123456789 ...
                    try:
                        chat_part = line.split(" - Chat ")[1].split(" ")[0]
                        chat_id = int(chat_part)
                        
                        # ФИЛЬТР: Добавляем только если ID отрицательный (группы/каналы)
                        # Личные чаты имеют положительный ID
                        if chat_id < 0:
                            chats.add(chat_id)
                            
                    except (ValueError, IndexError):
                        continue
    except FileNotFoundError:
        logger.warning(f"Лог-файл {USER_MESSAGES_LOG_PATH} не найден")
    
    return list(chats)

async def send_broadcast_message(bot, message_text: str):
    """Отправка рассылки во все найденные группы"""
    chats = await get_all_chats_from_log()
    
    if not chats:
        log_message("Нет подходящих чатов (групп) для рассылки")
        return 0, 0
    
    successful_sends = 0
    failed_sends = 0
    
    log_message(f"Начинаю рассылку в {len(chats)} групп")
    
    for chat_id in chats:
        try:
            await bot.send_message(chat_id, message_text)
            successful_sends += 1
            log_message(f"Рассылка успешно отправлена в чат {chat_id}")
            
            # Небольшая задержка между отправками во избежание флуд-лимитов
            await asyncio.sleep(0.1)
            
        except TelegramForbiddenError:
            failed_sends += 1
            log_message(f"Доступ запрещен в чат {chat_id} (бот удален или нет прав)")
            
        except TelegramBadRequest as e:
            failed_sends += 1
            log_message(f"Ошибка отправки в чат {chat_id}: {e}")
            
        except Exception as e:
            failed_sends += 1
            log_message(f"Неожиданная ошибка при отправке в чат {chat_id}: {e}")
    
    log_message(f"Рассылка завершена. Успешно: {successful_sends}, Неудачно: {failed_sends}")
    return successful_sends, failed_sends

async def handle_broadcast_command(message: types.Message):
    """Обработка команды рассылки"""
    # Проверка на админа
    if message.from_user.id != ADMIN_ID:
        # Можно вообще ничего не отвечать, чтобы не палить админку, 
        # но оставим ответ как в исходном коде
        await message.reply("❌ У вас нет прав для выполнения этой команды")
        return
    
    # Извлечение текста рассылки
    text = message.text
    # Используем lower() для проверки команды, но сохраняем регистр самого сообщения
    if not text or "упупа рассылка:" not in text.lower():
        await message.reply("❌ Неверный формат команды. Используйте: упупа рассылка: ваш текст")
        return
    
    # Получение текста после "упупа рассылка:"
    # split c maxsplit=1, чтобы не резать двоеточия в самом тексте сообщения
    try:
        parts = text.split(":", 1)
        if len(parts) < 2:
             await message.reply("❌ Пустой текст рассылки")
             return
        broadcast_text = parts[1].strip()
    except IndexError:
        return
    
    if not broadcast_text:
        await message.reply("❌ Текст рассылки не может быть пустым")
        return
    
    # Подтверждение начала
    await message.reply(f"🔄 Начинаю рассылку (только по группам):\n\n{broadcast_text}")
    
    # Запуск рассылки
    successful, failed = await send_broadcast_message(message.bot, broadcast_text)
    
    # Отчет
    result_text = f"✅ Рассылка по группам завершена!\n\n"
    result_text += f"📤 Успешно отправлено: {successful}\n"
    result_text += f"❌ Неудачных отправок: {failed}\n"
    result_text += f"📊 Всего попыток: {successful + failed}"
    
    await message.reply(result_text)

def extract_broadcast_text(text: str) -> str:
    """Вспомогательная функция для тестов или внешнего использования"""
    if "упупа рассылка:" in text.lower():
        try:
            return text.split(":", 1)[1].strip()
        except IndexError:
            return ""
    return ""

def is_broadcast_command(text: str) -> bool:
    """Проверка на команду"""
    return text and "упупа рассылка:" in text.lower()
