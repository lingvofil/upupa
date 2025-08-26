import logging
from aiogram import types, Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Импортируем глобальные переменные и функции сохранения из других модулей
from config import chat_settings, sms_disabled_chats, bot
from chat_settings import save_chat_settings
from sms_settings import save_sms_disabled_chats

# --- Вспомогательные функции ---

async def is_user_admin(chat_id: int, user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором чата."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except Exception as e:
        logging.error(f"Не удалось проверить статус администратора для user_id {user_id} в чате {chat_id}: {e}")
        return False

async def get_settings_markup(chat_id: str):
    """
    Создает текст сообщения и клавиатуру для меню настроек.
    Читает текущее состояние настроек и формирует соответствующий интерфейс.
    """
    # 1. Получаем текущие настройки
    # Настройка "Болталка"
    dialog_enabled = chat_settings.get(chat_id, {}).get("dialog_enabled", True)
    # Настройка "Реакции" (по умолчанию включены, если ключ отсутствует)
    reactions_enabled = chat_settings.get(chat_id, {}).get("reactions_enabled", True)
    # Настройка "СМС/ММС"
    sms_enabled = chat_id not in sms_disabled_chats

    # 2. Формируем текст сообщения
    text = "⚙️ *Настройки чата*\n\n"
    text += f"🗣️ *Болталка:* {'Вкл. ✅' if dialog_enabled else 'Выкл. ❌'}\n"
    text += f"_(Бот отвечает на обращения и в личке)_\n\n"
    text += f"🎉 *Случайные реакции:* {'Вкл. ✅' if reactions_enabled else 'Выкл. ❌'}\n"
    text += f"_(Бот иногда реагирует на случайные сообщения)_\n\n"
    text += f"💬 *СМС/ММС:* {'Вкл. ✅' if sms_enabled else 'Выкл. ❌'}\n"
    text += f"_(Возможность общаться с другими чатами)_\n"

    # 3. Создаем инлайн-клавиатуру
    builder = InlineKeyboardBuilder()
    
    # Кнопка для "Болталки"
    builder.button(
        text=f"{'Выключить' if dialog_enabled else 'Включить'} болталку",
        callback_data="settings:toggle:dialog"
    )
    # Кнопка для "Реакций"
    builder.button(
        text=f"{'Выключить' if reactions_enabled else 'Включить'} реакции",
        callback_data="settings:toggle:reactions"
    )
    # Кнопка для "СМС/ММС"
    builder.button(
        text=f"{'Выключить' if sms_enabled else 'Включить'} СМС/ММС",
        callback_data="settings:toggle:sms"
    )
    
    builder.adjust(1) # Располагаем кнопки по одной в строке
    return text, builder.as_markup()

# --- Основные обработчики ---

async def send_settings_menu(message: types.Message):
    """
    Обработчик команды 'упупа настройки'.
    Отправляет сообщение с текущими настройками и кнопками для их изменения.
    """
    if not await is_user_admin(message.chat.id, message.from_user.id):
        await message.reply("Настройки могут менять только админы, иди нахуй.")
        return

    chat_id = str(message.chat.id)
    text, markup = await get_settings_markup(chat_id)
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

async def handle_settings_callback(query: types.CallbackQuery):
    """
    Обработчик нажатий на инлайн-кнопки в меню настроек.
    Изменяет соответствующую настройку и обновляет исходное сообщение.
    """
    if not await is_user_admin(query.message.chat.id, query.from_user.id):
        await query.answer("Только админы могут менять настройки!", show_alert=True)
        return

    chat_id = str(query.message.chat.id)
    # Разбираем callback_data, например: "settings:toggle:dialog"
    try:
        action = query.data.split(":")[2]
    except IndexError:
        logging.warning(f"Некорректный callback_data: {query.data}")
        await query.answer("Произошла ошибка.")
        return

    # Инициализируем настройки для чата, если их нет
    if chat_id not in chat_settings:
        chat_settings[chat_id] = {
            "dialog_enabled": True,
            "reactions_enabled": True,
            "prompt": None
        }

    # Применяем изменения в зависимости от нажатой кнопки
    if action == "dialog":
        current_state = chat_settings[chat_id].get("dialog_enabled", True)
        chat_settings[chat_id]["dialog_enabled"] = not current_state
        save_chat_settings()
        await query.answer(f"Болталка {'выключена' if not chat_settings[chat_id]['dialog_enabled'] else 'включена'}.")
    
    elif action == "reactions":
        current_state = chat_settings[chat_id].get("reactions_enabled", True)
        chat_settings[chat_id]["reactions_enabled"] = not current_state
        save_chat_settings()
        await query.answer(f"Случайные реакции {'выключены' if not chat_settings[chat_id]['reactions_enabled'] else 'включены'}.")

    elif action == "sms":
        if chat_id in sms_disabled_chats:
            sms_disabled_chats.remove(chat_id)
            await query.answer("СМС/ММС включены.")
        else:
            sms_disabled_chats.add(chat_id)
            await query.answer("СМС/ММС выключены.")
        save_sms_disabled_chats()
    
    else:
        await query.answer("Неизвестное действие.")
        return

    # Обновляем сообщение с меню, чтобы отразить изменения
    text, markup = await get_settings_markup(chat_id)
    try:
        await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        # Может возникнуть ошибка, если сообщение не изменилось
        logging.info(f"Не удалось обновить сообщение настроек (возможно, оно не изменилось): {e}")

