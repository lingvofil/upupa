import logging
from aiogram import types, Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Импортируем глобальные переменные и функции сохранения из других модулей
# --- ИЗМЕНЕНИЕ: Добавляем импорт ADMIN_ID ---
from config import chat_settings, sms_disabled_chats, bot, ADMIN_ID
from chat_settings import save_chat_settings
from sms_settings import save_sms_disabled_chats
from prompts import PROMPTS_DICT

# --- Вспомогательные функции ---

# --- ИЗМЕНЕНИЕ: Функция переименована и обновлена ---
async def has_settings_permission(chat_id: int, user_id: int) -> bool:
    """
    Проверяет, есть ли у пользователя права на изменение настроек.
    Доступ разрешен, если пользователь является ADMIN_ID или администратором/создателем чата.
    """
    # Сначала проверяем, является ли пользователь владельцем бота
    if user_id == ADMIN_ID:
        return True
    
    # Если нет, проверяем, является ли он админом чата
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except Exception as e:
        logging.error(f"Не удалось проверить статус администратора для user_id {user_id} в чате {chat_id}: {e}")
        return False

# --- Функции для создания разметки (клавиатур) ---

async def get_main_settings_markup(chat_id: str):
    """
    Создает текст и клавиатуру для ГЛАВНОГО меню настроек.
    """
    settings = chat_settings.get(chat_id, {})
    dialog_enabled = settings.get("dialog_enabled", True)
    reactions_enabled = settings.get("reactions_enabled", True)
    sms_enabled = chat_id not in sms_disabled_chats
    current_prompt_name = settings.get("prompt_name", "Не установлен")

    text = "⚙️ *Настройки чата*\n\n"
    text += f"🗣️ *Болталка:* {'Вкл. ✅' if dialog_enabled else 'Выкл. ❌'}\n"
    text += f"🎉 *Случайные реакции:* {'Вкл. ✅' if reactions_enabled else 'Выкл. ❌'}\n"
    text += f"💬 *СМС/ММС:* {'Вкл. ✅' if sms_enabled else 'Выкл. ❌'}\n\n"
    text += f"🎭 *Текущий промпт:* `{current_prompt_name.capitalize()}`"

    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"{'Выключить' if dialog_enabled else 'Включить'} болталку",
        callback_data="settings:toggle:dialog"
    )
    builder.button(
        text=f"{'Выключить' if reactions_enabled else 'Включить'} реакции",
        callback_data="settings:toggle:reactions"
    )
    builder.button(
        text=f"{'Выключить' if sms_enabled else 'Включить'} СМС/ММС",
        callback_data="settings:toggle:sms"
    )
    builder.button(
        text="🎭 Выбрать промпт",
        callback_data="settings:view:prompts"
    )
    
    builder.adjust(1)
    return text, builder.as_markup()

async def get_prompts_markup():
    """
    Создает текст и клавиатуру для меню ВЫБОРА ПРОМПТА.
    """
    text = "🎭 *Выберите персонажа для бота*"
    
    builder = InlineKeyboardBuilder()
    
    for prompt_name in PROMPTS_DICT.keys():
        builder.button(
            text=prompt_name.capitalize(),
            callback_data=f"settings:set_prompt:{prompt_name}"
        )
    
    builder.button(text="⬅️ Назад", callback_data="settings:view:main")
    
    builder.adjust(2, 2, 2, 2, 2, 2, 2, 2, 2, 1) 
    
    return text, builder.as_markup()


# --- Основные обработчики ---

async def send_settings_menu(message: types.Message):
    """
    Обработчик команды 'упупа настройки'.
    Отправляет ГЛАВНОЕ меню настроек.
    """
    # --- ИЗМЕНЕНИЕ: Используем новую функцию проверки прав ---
    if not await has_settings_permission(message.chat.id, message.from_user.id):
        await message.reply("Настройки могут менять только админы, иди нахуй.")
        return

    chat_id = str(message.chat.id)
    text, markup = await get_main_settings_markup(chat_id)
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

async def handle_settings_callback(query: types.CallbackQuery):
    """
    Обработчик нажатий на ВСЕ инлайн-кнопки в меню настроек.
    """
    # --- ИЗМЕНЕНИЕ: Используем новую функцию проверки прав ---
    if not await has_settings_permission(query.message.chat.id, query.from_user.id):
        await query.answer("Только админы могут менять настройки!", show_alert=True)
        return

    chat_id = str(query.message.chat.id)
    
    try:
        _, action, value = query.data.split(":", 2)
    except ValueError:
        logging.warning(f"Некорректный callback_data: {query.data}")
        await query.answer("Произошла ошибка.")
        return

    if chat_id not in chat_settings:
        chat_settings[chat_id] = {
            "dialog_enabled": True,
            "reactions_enabled": True,
            "prompt": None
        }

    if action == "view":
        if value == "prompts":
            text, markup = await get_prompts_markup()
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        elif value == "main":
            text, markup = await get_main_settings_markup(chat_id)
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        await query.answer()
        return

    if action == "set_prompt":
        prompt_name = value
        prompt_text = PROMPTS_DICT.get(prompt_name)
        
        if prompt_text:
            settings = chat_settings[chat_id]
            settings["prompt"] = prompt_text
            settings["prompt_name"] = prompt_name
            settings["prompt_type"] = "standard"
            if "imitated_user" in settings:
                del settings["imitated_user"]
            save_chat_settings()
            await query.answer(f"Промпт изменен на: {prompt_name.capitalize()}")
            text, markup = await get_main_settings_markup(chat_id)
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:
            await query.answer("Такой промпт не найден!", show_alert=True)
        return

    if action == "toggle":
        if value == "dialog":
            current_state = chat_settings[chat_id].get("dialog_enabled", True)
            chat_settings[chat_id]["dialog_enabled"] = not current_state
            save_chat_settings()
        
        elif value == "reactions":
            current_state = chat_settings[chat_id].get("reactions_enabled", True)
            chat_settings[chat_id]["reactions_enabled"] = not current_state
            save_chat_settings()

        elif value == "sms":
            if chat_id in sms_disabled_chats:
                sms_disabled_chats.remove(chat_id)
            else:
                sms_disabled_chats.add(chat_id)
            save_sms_disabled_chats()
        
        else:
            await query.answer("Неизвестное действие.")
            return

        text, markup = await get_main_settings_markup(chat_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logging.info(f"Не удалось обновить сообщение настроек (возможно, оно не изменилось): {e}")
        
        await query.answer()
