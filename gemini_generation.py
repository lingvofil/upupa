import asyncio
import logging
import random
import base64
from io import BytesIO

from aiogram import types
from PIL import Image

# V-- ИМПОРТИРУЕМ СПЕЦИАЛЬНУЮ МОДЕЛЬ ДЛЯ ГЕНЕРАЦИИ КАРТИНОК --V
from config import image_model, bot 
from prompts import actions

# ... функция save_and_send_generated_image ...
async def save_and_send_generated_image(message: types.Message, image_data: bytes, caption: str = None):
    """Отправляет данные изображения пользователю как фото."""
    try:
        buffered_image = types.BufferedInputFile(image_data, filename="gemini_image.png")
        await message.reply_photo(buffered_image, caption=caption)
    except Exception as e:
        logging.error(f"Ошибка при отправке фото от Gemini: {e}")
        await message.reply("Не смог отправить картинку, что-то пошло не так.")


async def process_gemini_generation(prompt: str):
    """
    Основная логика генерации изображения через Gemini API.
    Возвращает кортеж (Успех, Сообщение об ошибке, Данные изображения, Текстовый ответ модели)
    """
    try:
        logging.info(f"Запрос к Gemini с промптом: {prompt}")
        
        # Теперь используем специальную модель image_model напрямую, это более правильно
        response = await asyncio.to_thread(
            image_model.generate_content, # <-- ИЗМЕНЕНО
            contents=prompt,
            generation_config={
                'response_modalities': ['TEXT', 'IMAGE']
            }
            # Больше не нужно указывать модель здесь в параметрах
        )
        
        image_data = None
        text_response = ""

        # Ищем в ответе части с изображением и текстом
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                # Найдена часть с изображением
                if part.inline_data.mime_type.startswith("image/"):
                    image_data = base64.b64decode(part.inline_data.data)
            elif part.text:
                # Найдена текстовая часть
                text_response += part.text + "\n"
        
        if image_data:
            logging.info("Изображение от Gemini успешно сгенерировано.")
            return True, None, image_data, text_response.strip()
        else:
            # Если изображения нет, но есть текст, это может быть отказ или пояснение
            error_message = text_response or "Gemini не вернул изображение. Возможно, запрос нарушает политику безопасности."
            logging.warning(f"Gemini не вернул изображение. Ответ: {error_message}")
            return False, error_message, None, None

    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        logging.error(f"Критическая ошибка в process_gemini_generation: {error_traceback}")
        # Возвращаем общее сообщение об ошибке
        return False, f"Произошла ошибка при обращении к Gemini API: {repr(e)}", None, None


async def handle_gemini_generation_command(message: types.Message):
    """
    Обработчик команды 'сгенерируй'.
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))

    prompt = None
    # Проверяем, есть ли текст после команды "сгенерируй"
    if message.text.lower().startswith("сгенерируй "):
        prompt = message.text[len("сгенерируй "):].strip()
    # Проверяем, является ли сообщение ответом на другое сообщение
    elif message.text.lower().strip() == "сгенерируй" and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption

    if not prompt:
        await message.reply("Что именно сгенерировать? Напиши после команды или ответь на сообщение.")
        return

    processing_message = await message.reply("Думаю над вашим запросом... 🤖")
    
    # Вызываем основную функцию генерации
    success, error_message, image_data, text_caption = await process_gemini_generation(prompt)
    
    await processing_message.delete()

    if success and image_data:
        # Если все успешно, отправляем картинку
        await save_and_send_generated_image(message, image_data, caption=text_caption)
    else:
        # Если произошла ошибка, сообщаем пользователю
        error_text = f"Не удалось сгенерировать изображение. Причина: {error_message}"
        await message.reply(error_text)
