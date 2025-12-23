import logging
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import chat_settings, sms_disabled_chats, bot, ADMIN_ID
from chat_settings import save_chat_settings
from sms_settings import save_sms_disabled_chats
from prompts import PROMPTS_DICT
from content_filter import ANTISPAM_ENABLED_CHATS, save_antispam_settings
from stat_rank_settings import rank_notifications_disabled_chats, save_rank_notifications_settings

# --- КОНСТАНТЫ ДЛЯ МЕНЮ ВЕРОЯТНОСТЕЙ ---
# Список доступных процентов для выбора
PROBABILITY_OPTIONS = [0, 0.001, 0.005, 0.008, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0]

# Красивые лейблы для кнопок
PROBABILITY_LABELS = {
    0: "0% (Выкл)", 0.001: "0.1%", 0.005: "0.5%", 0.008: "0.8%", 0.01: "1%",
    0.02: "2%", 0.05: "5%", 0.1: "10%", 0.2: "20%", 0.5: "50%", 1.0: "100%"
}

# Маппинг ключей настроек на названия кнопок
REACTION_TYPES = {
    "ai_prob": "🤖 Ремарки (AI)",
    "emoji_prob": "😎 Эмодзи",
    "meme_prob": "🖼 Случайные мемы",
    "voice_prob": "🗣 Голосовые",
    "regular_prob": "👖 Штаны",
    "rhyme_prob": "📝 Рифмы"
}

# Значения по умолчанию (если настройка еще не создана)
DEFAULT_PROBS = {
    "ai_prob": 0.01,
    "emoji_prob": 0.01,
    "meme_prob": 0.01,
    "voice_prob": 0.0001,
    "regular_prob": 0.008,
    "rhyme_prob": 0.008
}

async def has_settings_permission(chat_id: int, user_id: int) -> bool:
    """Проверка прав: только админы или создатель могут менять настройки."""
    if user_id == ADMIN_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except Exception as e:
        logging.error(f"Permission check error: {e}")
        return False

async def get_main_settings_markup(chat_id: str):
    """Генерация текста и кнопок главного меню настроек."""
    settings = chat_settings.get(chat_id, {})
    
    # Мастер-свитчи (вкл/выкл всей категории)
    dialog_enabled = settings.get("dialog_enabled", True)
    reactions_enabled = settings.get("reactions_enabled", True) 
    emoji_enabled = settings.get("emoji_enabled", True)
    random_memes_enabled = settings.get("random_memes_enabled", False)
    
    sms_enabled = chat_id not in sms_disabled_chats
    antispam_enabled = int(chat_id) in ANTISPAM_ENABLED_CHATS
    rank_notifications_enabled = chat_id not in rank_notifications_disabled_chats
    current_prompt_name = settings.get("prompt_name", "Не установлен")

    text = "⚙️ *Настройки чата*\n\n"
    text += f"🗣️ *Болталка:* {'Вкл. ✅' if dialog_enabled else 'Выкл. ❌'}\n"
    text += f"🎉 *Случайные ответы:* {'Вкл. ✅' if reactions_enabled else 'Выкл. ❌'}\n"
    text += f"👀 *Эмодзи-реакции:* {'Вкл. ✅' if emoji_enabled else 'Выкл. ❌'}\n"
    text += f"🖼️ *Случайные мемы:* {'Вкл. ✅' if random_memes_enabled else 'Выкл. ❌'}\n"
    text += f"💬 *СМС/ММС:* {'Вкл. ✅' if sms_enabled else 'Выкл. ❌'}\n"
    text += f"🛡️ *Антиспам-фильтр:* {'Вкл. ✅' if antispam_enabled else 'Выкл. ❌'}\n"
    text += f"🏅 *Уведомления о рангах:* {'Вкл. ✅' if rank_notifications_enabled else 'Выкл. ❌'}\n"
    text += f"🎭 *Текущий промпт:* `{current_prompt_name.capitalize()}`\n\n"
    text += "_Нажмите '📊 Настроить шансы', чтобы изменить частоту конкретных реакций._"

    builder = InlineKeyboardBuilder()
    # Основные переключатели
    builder.button(text=f"{'Выкл.' if dialog_enabled else 'Вкл.'} болталку", callback_data="settings:toggle:dialog")
    builder.button(text=f"{'Выкл.' if reactions_enabled else 'Вкл.'} ответы", callback_data="settings:toggle:reactions")
    builder.button(text=f"{'Выкл.' if emoji_enabled else 'Вкл.'} эмодзи", callback_data="settings:toggle:emojis")
    builder.button(text=f"{'Выкл.' if random_memes_enabled else 'Вкл.'} мемы", callback_data="settings:toggle:random_memes")
    builder.button(text=f"{'Выкл.' if sms_enabled else 'Вкл.'} СМС", callback_data="settings:toggle:sms")
    builder.button(text=f"{'Выкл.' if antispam_enabled else 'Вкл.'} антиспам", callback_data="settings:toggle:antispam")
    builder.button(text=f"{'Выкл.' if rank_notifications_enabled else 'Вкл.'} ранги", callback_data="settings:toggle:rank_notifications")
    
    # Кнопки перехода в подменю
    builder.button(text="📊 Настроить шансы", callback_data="settings:view:probs_menu")
    builder.button(text="🎭 Выбрать промпт", callback_data="settings:view:prompts")
    
    builder.adjust(2) 
    return text, builder.as_markup()

async def get_probs_menu_markup(chat_id: str):
    """Меню выбора типа реакции для настройки вероятности."""
    settings = chat_settings.get(chat_id, {})
    text = "📊 *Настройка вероятностей*\nВыберите тип реакции, чтобы изменить шанс срабатывания:\n\n"
    
    builder = InlineKeyboardBuilder()
    
    for key, label in REACTION_TYPES.items():
        # Получаем текущее значение или дефолтное
        current_val = settings.get(key, DEFAULT_PROBS.get(key, 0.01)) 
        
        # Форматируем для отображения (убираем лишние нули)
        percent_str = f"{current_val * 100:.3f}".rstrip('0').rstrip('.') + "%"
        
        text += f"{label}: `{percent_str}`\n"
        builder.button(text=f"{label} ({percent_str})", callback_data=f"settings:prob_type:{key}")

    builder.button(text="⬅️ Назад", callback_data="settings:view:main")
    builder.adjust(1)
    return text, builder.as_markup()

async def get_prob_value_markup(chat_id: str, prob_type: str):
    """Меню выбора конкретного процента."""
    label = REACTION_TYPES.get(prob_type, "Реакция")
    text = f"🎯 *Настройка: {label}*\nВыберите новый шанс срабатывания:"
    
    builder = InlineKeyboardBuilder()
    for val in PROBABILITY_OPTIONS:
        label_btn = PROBABILITY_LABELS.get(val, f"{val*100}%")
        builder.button(text=label_btn, callback_data=f"settings:set_prob:{prob_type}:{val}")
    
    builder.button(text="⬅️ Назад", callback_data="settings:view:probs_menu")
    builder.adjust(3)
    return text, builder.as_markup()

async def get_prompts_markup():
    """Меню выбора промптов."""
    text = "🎭 *Выберите персонажа для бота*"
    builder = InlineKeyboardBuilder()
    for prompt_name in PROMPTS_DICT.keys():
        builder.button(text=prompt_name.capitalize(), callback_data=f"settings:set_prompt:{prompt_name}")
    builder.button(text="⬅️ Назад", callback_data="settings:view:main")
    builder.adjust(2)
    return text, builder.as_markup()

async def send_settings_menu(message: types.Message):
    """Точка входа: отправляет меню настроек."""
    if not await has_settings_permission(message.chat.id, message.from_user.id):
        await message.reply("Настройки могут менять только админы.")
        return
    
    chat_id = str(message.chat.id)
    text, markup = await get_main_settings_markup(chat_id)
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

async def handle_settings_callback(query: types.CallbackQuery):
    """Обработчик нажатий на кнопки в меню настроек."""
    if not await has_settings_permission(query.message.chat.id, query.from_user.id):
        await query.answer("Только админы могут менять настройки!", show_alert=True)
        return

    chat_id = str(query.message.chat.id)
    try:
        parts = query.data.split(":")
        action = parts[1]
    except ValueError:
        return

    # --- НАВИГАЦИЯ ---
    if action == "view":
        target = parts[2]
        if target == "prompts":
            text, markup = await get_prompts_markup()
        elif target == "probs_menu":
            text, markup = await get_probs_menu_markup(chat_id)
        else: # main
            text, markup = await get_main_settings_markup(chat_id)
        
        await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        await query.answer()

    # --- ВЫБОР ТИПА ВЕРОЯТНОСТИ ---
    elif action == "prob_type":
        prob_key = parts[2]
        text, markup = await get_prob_value_markup(chat_id, prob_key)
        await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        await query.answer()

    # --- УСТАНОВКА ЗНАЧЕНИЯ ВЕРОЯТНОСТИ ---
    elif action == "set_prob":
        prob_key = parts[2]
        prob_val = float(parts[3])
        
        chat_settings.setdefault(chat_id, {})
        chat_settings[chat_id][prob_key] = prob_val
        save_chat_settings()
        
        await query.answer(f"Сохранено!")
        # Возвращаемся в меню выбора типа
        text, markup = await get_probs_menu_markup(chat_id)
        await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")

    # --- СМЕНА ПРОМПТА ---
    elif action == "set_prompt":
        prompt_name = parts[2]
        prompt_text = PROMPTS_DICT.get(prompt_name)
        if prompt_text:
            chat_settings.setdefault(chat_id, {})
            settings = chat_settings[chat_id]
            settings["prompt"] = prompt_text
            settings["prompt_name"] = prompt_name
            save_chat_settings()
            await query.answer(f"Промпт изменен на: {prompt_name.capitalize()}")
            text, markup = await get_main_settings_markup(chat_id)
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        else:
            await query.answer("Ошибка: промпт не найден.")

    # --- ПЕРЕКЛЮЧАТЕЛИ (TOGGLES) ---
    elif action == "toggle":
        value = parts[2]
        chat_settings.setdefault(chat_id, {})
        
        if value == "dialog":
            chat_settings[chat_id]["dialog_enabled"] = not chat_settings[chat_id].get("dialog_enabled", True)
        elif value == "reactions":
            chat_settings[chat_id]["reactions_enabled"] = not chat_settings[chat_id].get("reactions_enabled", True)
        elif value == "emojis":
            chat_settings[chat_id]["emoji_enabled"] = not chat_settings[chat_id].get("emoji_enabled", True)
        elif value == "random_memes":
            chat_settings[chat_id]["random_memes_enabled"] = not chat_settings[chat_id].get("random_memes_enabled", False)
        elif value == "sms":
            if chat_id in sms_disabled_chats: sms_disabled_chats.remove(chat_id)
            else: sms_disabled_chats.add(chat_id)
            save_sms_disabled_chats()
        elif value == "antispam":
            cid = int(chat_id)
            if cid in ANTISPAM_ENABLED_CHATS: ANTISPAM_ENABLED_CHATS.remove(cid)
            else: ANTISPAM_ENABLED_CHATS.add(cid)
            save_antispam_settings()
        elif value == "rank_notifications":
            if chat_id in rank_notifications_disabled_chats: rank_notifications_disabled_chats.remove(chat_id)
            else: rank_notifications_disabled_chats.add(chat_id)
            save_rank_notifications_settings()
        
        save_chat_settings()
        text, markup = await get_main_settings_markup(chat_id)
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        except:
            pass 
        await query.answer("Настройка сохранена")
