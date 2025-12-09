import asyncio
import logging
from datetime import datetime
from aiogram import types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from config import ADMIN_ID, LOG_FILE

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def log_message(message: str):
    """Логирование сообщений рассылки"""
    timestamp = datetime.now().isoformat()
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{timestamp} - BROADCAST - {message}\n")

async def get_all_chats_from_log():
    """Получение всех уникальных чатов из лог-файла"""
    chats = set()
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if " - Chat " in line:
                    # Извлекаем ID чата из строки лога
                    chat_part = line.split(" - Chat ")[1].split(" ")[0]
                    try:
                        chat_id = int(chat_part)
                        chats.add(chat_id)
                    except ValueError:
                        continue
    except FileNotFoundError:
        logger.warning("Лог-файл не найден")
    
    return list(chats)

async def send_broadcast_message(bot, message_text: str):
    """Отправка рассылки во все чаты"""
    chats = await get_all_chats_from_log()
    
    if not chats:
        log_message("Нет чатов для рассылки")
        return 0, 0
    
    successful_sends = 0
    failed_sends = 0
    
    log_message(f"Начинаю рассылку в {len(chats)} чатов")
    
    for chat_id in chats:
        try:
            await bot.send_message(chat_id, message_text)
            successful_sends += 1
            log_message(f"Рассылка успешно отправлена в чат {chat_id}")
            
            # Небольшая задержка между отправками
            await asyncio.sleep(0.1)
            
        except TelegramForbiddenError:
            failed_sends += 1
            log_message(f"Доступ запрещен в чат {chat_id} (бот удален или заблокирован)")
            
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
        await message.reply("❌ У вас нет прав для выполнения этой команды")
        return
    
    # Извлечение текста рассылки
    text = message.text
    if not text or "упупа рассылка:" not in text.lower():
        await message.reply("❌ Неверный формат команды. Используйте: упупа рассылка: ваш текст")
        return
    
    # Получение текста после "упупа рассылка:"
    broadcast_text = text.split(":", 1)[1].strip()
    
    if not broadcast_text:
        await message.reply("❌ Текст рассылки не может быть пустым")
        return
    
    # Подтверждение рассылки
    await message.reply(f"🔄 Начинаю рассылку сообщения:\n\n{broadcast_text}")
    
    # Отправка рассылки
    successful, failed = await send_broadcast_message(message.bot, broadcast_text)
    
    # Отчет о результатах
    result_text = f"✅ Рассылка завершена!\n\n"
    result_text += f"📤 Успешно отправлено: {successful}\n"
    result_text += f"❌ Неудачных отправок: {failed}\n"
    result_text += f"📊 Всего чатов: {successful + failed}"
    
    await message.reply(result_text)

def extract_broadcast_text(text: str) -> str:
    """Извлечение текста рассылки из команды"""
    if "упупа рассылка:" in text.lower():
        return text.split(":", 1)[1].strip()
    return ""

def is_broadcast_command(text: str) -> bool:
    """Проверка, является ли текст командой рассылки"""
    return text and "упупа рассылка:" in text.lower()
