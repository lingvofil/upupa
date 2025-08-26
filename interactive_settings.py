import logging
from aiogram import types, Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Импортируем глобальные переменные и функции сохранения из других модулей
from config import chat_settings, sms_disabled_chats, bot
from chat_settings import save_chat_settings
from sms_settings import save_sms_disabled_chats
# --- НОВОЕ: Импортируем словарь промптов ---
from prompts import PROMPTS_DICT

# --- Вспомогательные функции ---

async def is_user_admin(chat_id: int, user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором чата."""
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
    # 1. Получаем текущие настройки
    settings = chat_settings.get(chat_id, {})
    dialog_enabled = settings.get("dialog_enabled", True)
    reactions_enabled = settings.get("reactions_enabled", True)
    sms_enabled = chat_id not in sms_disabled_chats
    # --- НОВОЕ: Получаем имя текущего промпта ---
    current_prompt_name = settings.get("prompt_name", "Не установлен")

    # 2. Формируем текст сообщения
    text = "⚙️ *Настройки чата*\n\n"
    text += f"🗣️ *Болталка:* {'Вкл. ✅' if dialog_enabled else 'Выкл. ❌'}\n"
    text += f"🎉 *Случайные реакции:* {'Вкл. ✅' if reactions_enabled else 'Выкл. ❌'}\n"
    text += f"💬 *СМС/ММС:* {'Вкл. ✅' if sms_enabled else 'Выкл. ❌'}\n\n"
    # --- НОВОЕ: Отображаем текущий промпт ---
    text += f"🎭 *Текущий промпт:* `{current_prompt_name.capitalize()}`"


    # 3. Создаем инлайн-клавиатуру
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
    # --- НОВОЕ: Кнопка для перехода в меню выбора промпта ---
    builder.button(
        text="🎭 Выбрать промпт",
        callback_data="settings:view:prompts"
    )
    
    builder.adjust(1)
    return text, builder.as_markup()

async def get_prompts_markup():
    """
    --- НОВАЯ ФУНКЦИЯ ---
    Создает текст и клавиатуру для меню ВЫБОРА ПРОМПТА.
    """
    text = "🎭 *Выберите персонажа для бота*"
    
    builder = InlineKeyboardBuilder()
    
    # Создаем кнопки для каждого промпта из словаря
    for prompt_name in PROMPTS_DICT.keys():
        builder.button(
            text=prompt_name.capitalize(),
            callback_data=f"settings:set_prompt:{prompt_name}"
        )
    
    # Кнопка для возврата в главное меню
    builder.button(text="⬅️ Назад", callback_data="settings:view:main")
    
    # Расставляем кнопки по 2 в ряд, последняя (Назад) будет одна
    builder.adjust(2, 2, 2, 2, 2, 2, 2, 2, 2, 1) 
    
    return text, builder.as_markup()


# --- Основные обработчики ---

async def send_settings_menu(message: types.Message):
    """
    Обработчик команды 'упупа настройки'.
    Отправляет ГЛАВНОЕ меню настроек.
    """
    if not await is_user_admin(message.chat.id, message.from_user.id):
        await message.reply("Настройки могут менять только админы, иди нахуй.")
        return

    chat_id = str(message.chat.id)
    text, markup = await get_main_settings_markup(chat_id)
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

async def handle_settings_callback(query: types.CallbackQuery):
    """
    Обработчик нажатий на ВСЕ инлайн-кнопки в меню настроек.
    """
    if not await is_user_admin(query.message.chat.id, query.from_user.id):
        await query.answer("Только админы могут менять настройки!", show_alert=True)
        return

    chat_id = str(query.message.chat.id)
    
    try:
        # Разбираем callback_data, например: "settings:toggle:dialog" или "settings:view:prompts"
        _, action, value = query.data.split(":", 2)
    except ValueError:
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

    # --- НОВОЕ: Обработка переключения между меню ---
    if action == "view":
        if value == "prompts":
            # Показываем меню выбора промптов
            text, markup = await get_prompts_markup()
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        elif value == "main":
            # Показываем главное меню настроек
            text, markup = await get_main_settings_markup(chat_id)
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        await query.answer()
        return

    # --- НОВОЕ: Обработка установки промпта ---
    if action == "set_prompt":
        prompt_name = value
        prompt_text = PROMPTS_DICT.get(prompt_name)
        
        if prompt_text:
            settings = chat_settings[chat_id]
            settings["prompt"] = prompt_text
            settings["prompt_name"] = prompt_name
            settings["prompt_type"] = "standard" # Указываем, что это стандартный промпт
            if "imitated_user" in settings:
                del settings["imitated_user"] # Сбрасываем имитацию пользователя
            save_chat_settings()
            await query.answer(f"Промпт изменен на: {prompt_name.capitalize()}")
            # Возвращаемся в главное меню, чтобы показать изменения
            text, markup = await get_main_settings_markup(chat_id)
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:
            await query.answer("Такой промпт не найден!", show_alert=True)
        return

    # Обработка переключателей (toggle)
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

        # Обновляем главное меню, чтобы отразить изменения
        text, markup = await get_main_settings_markup(chat_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logging.info(f"Не удалось обновить сообщение настроек (возможно, оно не изменилось): {e}")
        
        await query.answer() # Отвечаем на колбэк, чтобы убрать "часики"
