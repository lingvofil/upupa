import logging
from aiogram import types, Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import chat_settings, sms_disabled_chats, bot, ADMIN_ID
from chat_settings import save_chat_settings
from sms_settings import save_sms_disabled_chats
from prompts import PROMPTS_DICT
from content_filter import ANTISPAM_ENABLED_CHATS, save_antispam_settings
from stat_rank_settings import rank_notifications_disabled_chats, save_rank_notifications_settings

async def has_settings_permission(chat_id: int, user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ["administrator", "creator"]
    except Exception as e:
        logging.error(f"Не удалось проверить статус администратора: {e}")
        return False

async def get_main_settings_markup(chat_id: str):
    settings = chat_settings.get(chat_id, {})
    dialog_enabled = settings.get("dialog_enabled", True)
    reactions_enabled = settings.get("reactions_enabled", True)
    random_memes_enabled = settings.get("random_memes_enabled", False) # По умолчанию выключено
    sms_enabled = chat_id not in sms_disabled_chats
    antispam_enabled = int(chat_id) in ANTISPAM_ENABLED_CHATS
    rank_notifications_enabled = chat_id not in rank_notifications_disabled_chats
    current_prompt_name = settings.get("prompt_name", "Не установлен")

    text = "⚙️ *Настройки чата*\n\n"
    text += f"🗣️ *Болталка:* {'Вкл. ✅' if dialog_enabled else 'Выкл. ❌'}\n"
    text += f"🎉 *Случайные реакции:* {'Вкл. ✅' if reactions_enabled else 'Выкл. ❌'}\n"
    text += f"🖼️ *Случайные мемы (1%):* {'Вкл. ✅' if random_memes_enabled else 'Выкл. ❌'}\n"
    text += f"💬 *СМС/ММС:* {'Вкл. ✅' if sms_enabled else 'Выкл. ❌'}\n"
    text += f"🛡️ *Антиспам-фильтр:* {'Вкл. ✅' if antispam_enabled else 'Выкл. ❌'}\n"
    text += f"🏅 *Уведомления о рангах:* {'Вкл. ✅' if rank_notifications_enabled else 'Выкл. ❌'}\n\n"
    text += f"🎭 *Текущий промпт:* `{current_prompt_name.capitalize()}`"

    builder = InlineKeyboardBuilder()
    builder.button(text=f"{'Выкл.' if dialog_enabled else 'Вкл.'} болталку", callback_data="settings:toggle:dialog")
    builder.button(text=f"{'Выкл.' if reactions_enabled else 'Вкл.'} реакции", callback_data="settings:toggle:reactions")
    builder.button(text=f"{'Выкл.' if random_memes_enabled else 'Вкл.'} случайные мемы", callback_data="settings:toggle:random_memes")
    builder.button(text=f"{'Выкл.' if sms_enabled else 'Вкл.'} СМС/ММС", callback_data="settings:toggle:sms")
    builder.button(text=f"{'Выкл.' if antispam_enabled else 'Вкл.'} антиспам", callback_data="settings:toggle:antispam")
    builder.button(text=f"{'Выкл.' if rank_notifications_enabled else 'Вкл.'} уведомления о рангах", callback_data="settings:toggle:rank_notifications")
    builder.button(text="🎭 Выбрать промпт", callback_data="settings:view:prompts")
    
    builder.adjust(1)
    return text, builder.as_markup()

async def handle_settings_callback(query: types.CallbackQuery):
    if not await has_settings_permission(query.message.chat.id, query.from_user.id):
        await query.answer("Только админы могут менять настройки!", show_alert=True)
        return

    chat_id = str(query.message.chat.id)
    try:
        _, action, value = query.data.split(":", 2)
    except ValueError:
        return

    if action == "view":
        if value == "prompts":
            text, markup = await get_prompts_markup()
        elif value == "main":
            text, markup = await get_main_settings_markup(chat_id)
        else:
            return
        await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        await query.answer()
        return

    if action == "toggle":
        chat_settings.setdefault(chat_id, {})
        
        if value == "dialog":
            chat_settings[chat_id]["dialog_enabled"] = not chat_settings[chat_id].get("dialog_enabled", True)
        elif value == "reactions":
            chat_settings[chat_id]["reactions_enabled"] = not chat_settings[chat_id].get("reactions_enabled", True)
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
        await query.answer("Настройки обновлены")
