#interactive_settings.py

import logging
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import chat_settings, sms_disabled_chats, bot, ADMIN_ID
from chat_settings import save_chat_settings
from sms_settings import save_sms_disabled_chats
# Добавляем HELP_DICT в импорт
from prompts import PROMPTS_DICT, HELP_DICT
from content_filter import ANTISPAM_ENABLED_CHATS, save_antispam_settings
from stat_rank_settings import rank_notifications_disabled_chats, save_rank_notifications_settings

# --- КОНСТАНТЫ ДЛЯ МЕНЮ ВЕРОЯТНОСТЕЙ ---
PROBABILITY_OPTIONS = [0, 0.001, 0.005, 0.008, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0]

PROBABILITY_LABELS = {
    0: "0% (Выкл)", 0.001: "0.1%", 0.005: "0.5%", 0.008: "0.8%", 0.01: "1%",
    0.02: "2%", 0.05: "5%", 0.1: "10%", 0.2: "20%", 0.5: "50%", 1.0: "100%"
}

REACTION_TYPES = {
    "ai_prob": "🤖 Ремарки (AI)",
    "random_word_prob": "🗣️ Я %слово%", # НОВОЕ
    "emoji_prob": "😎 Эмодзи",
    "meme_prob": "🖼 Случайные мемы",
    "voice_prob": "🗣 Голосовые",
    "regular_prob": "👖 Штаны",
    "rhyme_prob": "📝 Рифмы"
}

DEFAULT_PROBS = {
    "ai_prob": 0.01,
    "random_word_prob": 0.005, # НОВОЕ: Default 0.5%
    "emoji_prob": 0.01,
    "meme_prob": 0.01,
    "voice_prob": 0.0001,
    "regular_prob": 0.008,
    "rhyme_prob": 0.008
}

YTP_EFFECTS = {
    "stutter": "🔫 Заикание",
    "ping_pong": "🏓 Пинг-понг",
    "reverse": "⏪ Реверс",
    "invert": "🎨 Негатив",
    "earrape": "📢 Earrape",
    "speedup": "⚡ Ускорение",
    "slowmo": "🐌 Замедление",
    "mirror": "↔️ Зеркало X",
    "zoom_punch": "🔍 Зум",
    "rotate": "🌀 Поворот",
    "freeze_frame": "⏸ Стоп-кадр",
    "strobe": "💡 Строб",
    "triple_repeat": "🔁 Тройной повтор",
    "mirror_y": "↕️ Зеркало Y",
    "brightness_flash": "💥 Вспышка",
    "silence": "🔇 Тишина",
    "normal": "😐 Без эффекта",
}

YTP_DURATION_OPTIONS = [5, 7, 10, 15, 20, 30]

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

# ========================= ЛОГИКА НАСТРОЕК =========================

async def get_main_settings_markup(chat_id: str):
    settings = chat_settings.get(chat_id, {})
    
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
    builder.button(text=f"{'Выкл.' if dialog_enabled else 'Вкл.'} болталку", callback_data="settings:toggle:dialog")
    builder.button(text=f"{'Выкл.' if reactions_enabled else 'Вкл.'} ответы", callback_data="settings:toggle:reactions")
    builder.button(text=f"{'Выкл.' if emoji_enabled else 'Вкл.'} эмодзи", callback_data="settings:toggle:emojis")
    builder.button(text=f"{'Выкл.' if random_memes_enabled else 'Вкл.'} мемы", callback_data="settings:toggle:random_memes")
    builder.button(text=f"{'Выкл.' if sms_enabled else 'Вкл.'} СМС", callback_data="settings:toggle:sms")
    builder.button(text=f"{'Выкл.' if antispam_enabled else 'Вкл.'} антиспам", callback_data="settings:toggle:antispam")
    builder.button(text=f"{'Выкл.' if rank_notifications_enabled else 'Вкл.'} ранги", callback_data="settings:toggle:rank_notifications")
    
    builder.button(text="📊 Настроить шансы", callback_data="settings:view:probs_menu")
    builder.button(text="🎭 Выбрать промпт", callback_data="settings:view:prompts")
    builder.button(text="🎬 Настройки YTP", callback_data="settings:view:ytp_menu")
    
    builder.adjust(2) 
    return text, builder.as_markup()

async def get_probs_menu_markup(chat_id: str):
    settings = chat_settings.get(chat_id, {})
    text = "📊 *Настройка вероятностей*\nВыберите тип реакции, чтобы изменить шанс срабатывания:\n\n"
    
    builder = InlineKeyboardBuilder()
    
    for key, label in REACTION_TYPES.items():
        current_val = settings.get(key, DEFAULT_PROBS.get(key, 0.01)) 
        percent_str = f"{current_val * 100:.3f}".rstrip('0').rstrip('.') + "%"
        text += f"{label}: `{percent_str}`\n"
        builder.button(text=f"{label} ({percent_str})", callback_data=f"settings:prob_type:{key}")

    builder.button(text="⬅️ Назад", callback_data="settings:view:main")
    builder.adjust(1)
    return text, builder.as_markup()

async def get_prob_value_markup(chat_id: str, prob_type: str):
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
    text = "🎭 *Выберите персонажа для бота*"
    builder = InlineKeyboardBuilder()
    for prompt_name in PROMPTS_DICT.keys():
        builder.button(text=prompt_name.capitalize(), callback_data=f"settings:set_prompt:{prompt_name}")
    builder.button(text="⬅️ Назад", callback_data="settings:view:main")
    builder.adjust(2)
    return text, builder.as_markup()

async def get_ytp_menu_markup(chat_id: str):
    settings = chat_settings.get(chat_id, {})
    disabled = set(settings.get("ytp_disabled_effects", []))
    duration = settings.get("ytp_duration", 10)

    text = "🎬 *Настройки YTP*\n\n"
    text += f"⏱ Длина видео: `{duration} сек`\n\n"
    text += "Включённые эффекты (нажми чтобы выкл):\n"

    builder = InlineKeyboardBuilder()
    builder.button(text=f"⏱ Длина: {duration} сек", callback_data="settings:ytp:duration_menu")
    builder.adjust(1)

    for key, label in YTP_EFFECTS.items():
        is_on = key not in disabled
        btn_text = f"{'✅' if is_on else '❌'} {label}"
        builder.button(text=btn_text, callback_data=f"settings:ytp:toggle_effect:{key}")

    builder.button(text="⬅️ Назад", callback_data="settings:view:main")
    builder.adjust(1, 2)
    return text, builder.as_markup()

async def get_ytp_duration_markup(chat_id: str):
    settings = chat_settings.get(chat_id, {})
    current = settings.get("ytp_duration", 10)

    text = "⏱ *Длина YTP-видео (секунды)*\nВыберите целевую длину:"
    builder = InlineKeyboardBuilder()

    for val in YTP_DURATION_OPTIONS:
        marker = "▶️ " if val == current else ""
        builder.button(text=f"{marker}{val} сек", callback_data=f"settings:ytp:set_duration:{val}")

    builder.button(text="⬅️ Назад", callback_data="settings:ytp:menu")
    builder.adjust(3)
    return text, builder.as_markup()

async def send_settings_menu(message: types.Message):
    if not await has_settings_permission(message.chat.id, message.from_user.id):
        await message.reply("Настройки могут менять только админы.")
        return
    
    chat_id = str(message.chat.id)
    text, markup = await get_main_settings_markup(chat_id)
    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

async def handle_settings_callback(query: types.CallbackQuery):
    if not await has_settings_permission(query.message.chat.id, query.from_user.id):
        await query.answer("Только админы могут менять настройки!", show_alert=True)
        return

    chat_id = str(query.message.chat.id)
    try:
        parts = query.data.split(":")
        action = parts[1]
    except ValueError:
        return

    if action == "view":
        target = parts[2]
        if target == "prompts":
            text, markup = await get_prompts_markup()
        elif target == "probs_menu":
            text, markup = await get_probs_menu_markup(chat_id)
        elif target == "ytp_menu":
            text, markup = await get_ytp_menu_markup(chat_id)
        else: # main
            text, markup = await get_main_settings_markup(chat_id)
        
        await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        await query.answer()

    elif action == "prob_type":
        prob_key = parts[2]
        text, markup = await get_prob_value_markup(chat_id, prob_key)
        await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        await query.answer()

    elif action == "set_prob":
        prob_key = parts[2]
        prob_val = float(parts[3])
        
        chat_settings.setdefault(chat_id, {})
        chat_settings[chat_id][prob_key] = prob_val
        save_chat_settings()
        
        await query.answer(f"Сохранено!")
        text, markup = await get_probs_menu_markup(chat_id)
        await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")

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

    elif action == "ytp":
        sub = parts[2]

        if sub == "menu":
            text, markup = await get_ytp_menu_markup(chat_id)
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
            await query.answer()

        elif sub == "duration_menu":
            text, markup = await get_ytp_duration_markup(chat_id)
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
            await query.answer()

        elif sub == "set_duration":
            val = int(parts[3])
            chat_settings.setdefault(chat_id, {})
            chat_settings[chat_id]["ytp_duration"] = val
            save_chat_settings()
            await query.answer(f"Длина установлена: {val} сек")
            text, markup = await get_ytp_duration_markup(chat_id)
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")

        elif sub == "toggle_effect":
            effect_key = parts[3]
            chat_settings.setdefault(chat_id, {})
            disabled = set(chat_settings[chat_id].get("ytp_disabled_effects", []))
            if effect_key in disabled:
                disabled.remove(effect_key)
                await query.answer("✅ Эффект включён")
            else:
                disabled.add(effect_key)
                await query.answer("❌ Эффект выключен")
            chat_settings[chat_id]["ytp_disabled_effects"] = list(disabled)
            save_chat_settings()
            text, markup = await get_ytp_menu_markup(chat_id)
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")

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

# ========================= ЛОГИКА СПРАВКИ (HELP MENU) =========================

def get_help_keyboard(current_section="main"):
    """Генерация клавиатуры для навигации по справке."""
    builder = InlineKeyboardBuilder()
    
    # Кнопки разделов
    builder.row(
        types.InlineKeyboardButton(text="🗣 Говорилка", callback_data="help:talking"),
        types.InlineKeyboardButton(text="📊 Стотистика и оналез", callback_data="help:stats")
    )
    builder.row(
        types.InlineKeyboardButton(text="🎮 Векторины и игры", callback_data="help:creative"),
        types.InlineKeyboardButton(text="🎨 Медиа", callback_data="help:media")
    )
    builder.row(
        types.InlineKeyboardButton(text="📺 Коналы", callback_data="help:content"),
        types.InlineKeyboardButton(text="🖼 Всякая хуйня", callback_data="help:utils")
    )
    
    # Кнопка "В ночало", если мы не в главном меню
    if current_section != "main":
        builder.row(
            types.InlineKeyboardButton(text="🔙 В ночало", callback_data="help:main")
        )
        
    return builder.as_markup()

async def send_help_menu(message: types.Message):
    """Отправка главного меню справки."""
    try:
        await message.answer(
            text=HELP_DICT["main"],
            reply_markup=get_help_keyboard("main"),
            parse_mode="HTML"
        )
    except Exception as e:
        # Fallback если словарь не прогрузился или ошибка
        await message.reply("Справка недоступна (ошибка словаря).")
        logging.error(f"Help menu error: {e}")

async def handle_help_callback(query: types.CallbackQuery):
    """Обработка нажатий на кнопки справки."""
    # Парсим callback data "help:section_name"
    try:
        section = query.data.split(":")[1]
    except IndexError:
        section = "main"
    
    # Получаем текст из словаря
    text = HELP_DICT.get(section, HELP_DICT["main"])
    keyboard = get_help_keyboard(section)
    
    try:
        # Проверяем, изменился ли текст, чтобы не словить ошибку Telegram
        if query.message.text != text and query.message.caption != text: 
            await query.message.edit_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
    except Exception as e:
        pass # Игнорируем, если контент тот же
    
    await query.answer()
