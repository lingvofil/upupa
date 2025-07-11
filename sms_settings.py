import os
import json
import logging
from aiogram import Bot, types
from config import SMS_DISABLED_CHATS_FILE, SPECIAL_CHAT_ID

# ✅ Функция загрузки списка чатов с отключёнными смс
def load_sms_disabled_chats():
    global sms_disabled_chats
    if os.path.exists(SMS_DISABLED_CHATS_FILE):
        try:
            with open(SMS_DISABLED_CHATS_FILE, "r", encoding="utf-8") as file:
                sms_disabled_chats = set(json.load(file))
                logging.info(f"Загружено {len(sms_disabled_chats)} чатов с отключёнными смс.")
        except Exception as e:
            logging.error(f"Ошибка при загрузке списка отключённых смс: {e}")
            sms_disabled_chats = set()
    else:
        sms_disabled_chats = set()

# ✅ Функция сохранения списка чатов с отключёнными смс
def save_sms_disabled_chats():
    try:
        with open(SMS_DISABLED_CHATS_FILE, "w", encoding="utf-8") as file:
            json.dump(list(sms_disabled_chats), file, ensure_ascii=False, indent=4)
        logging.info("Список чатов с отключёнными смс сохранён.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении списка отключённых смс: {e}")

# ✅ Загружаем список отключённых чатов при старте бота
load_sms_disabled_chats()

# Вынесенная логика отключения СМС
async def process_disable_sms(chat_id, user_id, bot):
    # Проверяем, является ли пользователь админом или суперюзером
    chat_member = await bot.get_chat_member(chat_id, user_id)
    is_admin = chat_member.status in ["administrator", "creator"]
    is_superuser = user_id == 126386976  # 👑 Наш суперюзер
    
    if not (is_admin or is_superuser):
        return "Ты не админ и не бог, иди нахуй."
    
    if chat_id in sms_disabled_chats:
        return "СМС и ММС уже отключены в этом чате."
    else:
        sms_disabled_chats.add(chat_id)
        save_sms_disabled_chats()
        return "Теперь я не принимаю и не отправляю смс и ммс в этом чате."

# Вынесенная логика включения СМС
async def process_enable_sms(chat_id, user_id, bot):
    # Проверяем, является ли пользователь админом
    chat_member = await bot.get_chat_member(chat_id, user_id)
    if chat_member.status not in ["administrator", "creator"]:
        return "Ты не админ, иди нахуй."
    
    if chat_id in sms_disabled_chats:
        sms_disabled_chats.remove(chat_id)
        save_sms_disabled_chats()
        return "Теперь я снова принимаю и отправляю смс и ммс в этом чате."
    else:
        return "СМС и ММС уже разрешены в этом чате."

# Вынесенная логика отправки СМС
async def process_send_sms(message, chat_list, bot, sms_disabled_chats):
    chat_id = str(message.chat.id)
    is_reply = message.reply_to_message is not None  # Проверяем, это реплай или нет
    parts = message.text.split(maxsplit=2)  # Разделяем команду
    
    if len(parts) < 2 and not is_reply:
        await message.reply("эээ далбаеб: смс <номер чата> <текст>")
        return
    
    try:
        chat_index = int(parts[1]) - 1
        
        # Фильтруем чаты без названия (где title == None)
        filtered_chats = [chat for chat in chat_list if chat.get("title")]
        # Сортируем чаты для правильного определения индекса
        filtered_chats.sort(key=lambda chat: 0 if chat["id"] == SPECIAL_CHAT_ID else 1)
        
        if chat_index < 0 or chat_index >= len(filtered_chats):
            await message.reply("Чат с таким номером не найден, иди нахуй")
            return
            
        target_chat_id = str(filtered_chats[chat_index]["id"])
        # Проверяем, отключены ли СМС в целевом чате
        if target_chat_id in sms_disabled_chats:
            await message.reply("Этот чат не принимает СМС.")
            return
            
        source_chat_title = message.chat.title or "Неизвестный чат"
        # Находим номер исходного чата в отсортированном списке
        source_chat_number = next((i+1 for i, chat in enumerate(filtered_chats) if str(chat["id"]) == chat_id), "❓")
        
        if is_reply:
            original_text = message.reply_to_message.text or message.reply_to_message.caption or "(без текста)"
            formatted_message = f'Вам песьмо из чата "{source_chat_title}" (Чат #{source_chat_number}):\n\n{original_text}'
            await bot.send_message(target_chat_id, formatted_message)
            await message.reply(f"Песьмо отправлено в чат {filtered_chats[chat_index]['title']}!")
            return
            
        text_message = parts[2] if len(parts) > 2 else "(без текста)"
        formatted_message = f'Вам песьмо из чата "{source_chat_title}" (Чат #{source_chat_number}):\n\n{text_message}'
        await bot.send_message(target_chat_id, formatted_message)
        await message.reply(f"Песьмо отправлено в чат {filtered_chats[chat_index]['title']}!")
        
    except ValueError:
        await message.reply("Неверный формат, дурачок. Используй: смс <номер чата> <текст>")
    except Exception as e:
        logging.error(f"Ошибка при отправке сообщения в чат: {e}")
        await message.reply("Не удалось отправить сообщение. Возможно, я хуисос")

# Вынесенная логика отправки ММС
async def process_send_mms(message, chat_list_param, bot, sms_disabled_chats):
    chat_list = chat_list_param  # Используем локальную переменную вместо global
    chat_id = str(message.chat.id)

    is_reply = message.reply_to_message is not None

    command_text = message.text or message.caption  
    parts = command_text.split(maxsplit=2)  

    if len(parts) < 2 and not is_reply:
        await message.reply("эээ далбаеб: ммс <номер чата> (и прикрепи медиафайл)")
        return

    try:
        chat_index = int(parts[1]) - 1
        
        # Фильтруем чаты без названия (где title == None)
        filtered_chats = [chat for chat in chat_list if chat.get("title")]
        # Сортируем чаты для правильного определения индекса
        filtered_chats.sort(key=lambda chat: 0 if chat["id"] == SPECIAL_CHAT_ID else 1)
        
        if chat_index < 0 or chat_index >= len(filtered_chats):
            await message.reply("Чат с таким номером не найден, иди нахуй")
            return

        target_chat_id = str(filtered_chats[chat_index]["id"])

        # Проверяем, отключены ли ММС в целевом чате
        if target_chat_id in sms_disabled_chats:
            await message.reply("Этот чат не принимает ММС.")
            return

        source_chat_title = message.chat.title or "Неизвестный чат"
        # Находим номер исходного чата в отсортированном списке
        source_chat_number = next((i+1 for i, chat in enumerate(filtered_chats) if str(chat["id"]) == chat_id), "❓")
        user_text = parts[2] if len(parts) > 2 else ""  
        caption = f'Вам аткрытка из чата "{source_chat_title}" (Чат #{source_chat_number}):\n\n{user_text}'

        media = None

        # ✅ Если это реплай на медиа, пересылаем его
        if is_reply and message.reply_to_message:
            if message.reply_to_message.photo:
                media = message.reply_to_message.photo[-1].file_id
                await bot.send_photo(target_chat_id, media, caption=caption)

            elif message.reply_to_message.video:
                media = message.reply_to_message.video.file_id
                await bot.send_video(target_chat_id, media, caption=caption)

            elif message.reply_to_message.animation:
                media = message.reply_to_message.animation.file_id
                await bot.send_animation(target_chat_id, media, caption=caption)

            elif message.reply_to_message.audio:
                media = message.reply_to_message.audio.file_id
                await bot.send_audio(target_chat_id, media, caption=caption)

            elif message.reply_to_message.voice:
                media = message.reply_to_message.voice.file_id
                await bot.send_voice(target_chat_id, media, caption=caption)

            elif message.reply_to_message.document:
                media = message.reply_to_message.document.file_id
                await bot.send_document(target_chat_id, media, caption=caption)

            elif message.reply_to_message.sticker:
                media = message.reply_to_message.sticker.file_id
                await bot.send_sticker(target_chat_id, media)

            elif message.reply_to_message.poll:
                poll = message.reply_to_message.poll
                await bot.send_poll(
                    chat_id=target_chat_id,
                    question=poll.question,
                    options=[option.text for option in poll.options],
                    type='quiz' if poll.type == 'quiz' else 'regular',
                    correct_option_id=poll.correct_option_id if poll.type == 'quiz' else None,
                    explanation=poll.explanation,
                    is_anonymous=False  # Добавляем этот параметр
                )

            await message.reply(f"Аткрытка отправлена в чат {chat_list[chat_index]['title']}!")
            return

        # ✅ Если это обычное "ммс", отправляем медиа
        if message.photo:
            media = message.photo[-1].file_id
            await bot.send_photo(target_chat_id, media, caption=caption)

        elif message.video:
            media = message.video.file_id
            await bot.send_video(target_chat_id, media, caption=caption)

        elif message.animation:
            media = message.animation.file_id
            await bot.send_animation(target_chat_id, media, caption=caption)

        elif message.audio:
            media = message.audio.file_id
            await bot.send_audio(target_chat_id, media, caption=caption)

        elif message.voice:
            media = message.voice.file_id
            await bot.send_voice(target_chat_id, media, caption=caption)

        elif message.document:
            media = message.document.file_id
            await bot.send_document(target_chat_id, media, caption=caption)

        elif message.sticker:
            media = message.sticker.file_id
            await bot.send_sticker(target_chat_id, media)

        if media:
            await message.reply(f"Аткрытка отправлена в чат {filtered_chats[chat_index]['title']}!")
        else:
            await message.reply("Ошибка блядь: не удалось отправить медиа.")

    except ValueError:
        await message.reply("Неверный формат, дурачок. Используй: ммс <номер чата> (и прикрепи медиафайл)")
    except Exception as e:
        logging.error(f"Ошибка при отправке аткрытки в чат: {e}")
        await message.reply("Не удалось отправить медиа. Возможно, я хуисос")
