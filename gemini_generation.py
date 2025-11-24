import asyncio
import logging
import random
import base64
from io import BytesIO

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from PIL import Image

# Импортируем из конфига, как обычно
from config import image_model, bot 
from prompts import actions
# from picgeneration import (
#     get_image_data, process_image_for_telegram, download_file_bytes
# ) # Если у вас есть такие хелперы, убедитесь, что они доступны

# Убедитесь, что этот хелпер определен в вашем picgeneration.py
def is_valid_image_data(data: bytes) -> bool:
    """Проверяет сигнатуры известных форматов изображений."""
    if data.startswith(b'\x89PNG') or data.startswith(b'\xff\xd8') or data.startswith(b'RIFF'):
        return True
    return False

# Убедитесь, что этот хелпер определен в вашем picgeneration.py
async def save_and_send_generated_image(message: types.Message, image_data: bytes):
    """Пытается отправить изображение, при ошибке использует Pillow для конвертации."""
    try:
        logging.info("Попытка №1: отправка необработанных данных изображения...")
        raw_buffered_image = types.BufferedInputFile(image_data, filename="gemini_image_raw.png")
        await message.reply_photo(raw_buffered_image)
        logging.info("Необработанные данные успешно отправлены.")
    except TelegramBadRequest:
        logging.warning("Попытка №1 не удалась. Запускаю Pillow.")
        try:
            image = Image.open(BytesIO(image_data))
            output_buffer = BytesIO()
            image.save(output_buffer, 'PNG')
            output_buffer.seek(0)
            processed_buffered_image = types.BufferedInputFile(output_buffer.read(), filename="gemini_image_processed.png")
            await message.reply_photo(processed_buffered_image)
            logging.info("Обработанное через Pillow изображение успешно отправлено.")
        except Exception as pil_error:
            logging.error(f"Pillow не смог обработать: {pil_error}")
            await message.reply("API вернуло данные, которые не являются изображением.")

# === ИСПРАВЛЕННАЯ ФУНКЦИЯ ГЕНЕРАЦИИ ИЗОБРАЖЕНИЙ ===
async def process_gemini_image_generation(prompt: str):
    """
    Основная функция генерации изображения через Gemini API.
    Использует gemini-2.0-flash с явным запросом модальности IMAGE.
    """
    try:
        logging.info(f"Запрос к Gemini 2.0 Flash для генерации изображения с промптом: {prompt}")
        
        def sync_call():
            return image_model.generate_content(
                contents=prompt,
                generation_config={
                    # ЭТОТ ПАРАМЕТР КРИТИЧЕН для gemini-2.0-flash, когда 
                    # мы ожидаем только изображение.
                    'response_modalities': ['IMAGE'] 
                }
            )

        response = await asyncio.to_thread(sync_call)

        image_data = None
        text_response = ""

        # Проверка на наличие ответа
        if not response.parts:
             return 'FAILURE', {"error": "Модель вернула пустой ответ (возможно, сработал Safety Filter)."}


        for part in response.parts:
            # 1. Обработка inline_data (изображения)
            if hasattr(part, "inline_data") and part.inline_data:
                mime_type = getattr(part.inline_data, "mime_type", "unknown")
                logging.info(f"Gemini вернул MIME-тип: {mime_type}")
                raw_data = part.inline_data.data
                
                if isinstance(raw_data, str):
                    try:
                        image_data = base64.b64decode(raw_data)
                    except Exception:
                        image_data = raw_data.encode("latin1", errors="ignore")
                elif isinstance(raw_data, bytes):
                    image_data = raw_data
            
            # 2. Обработка текста (если модель вернула отказ)
            elif hasattr(part, "text") and part.text:
                text_response += part.text.strip()


        if image_data:
            if not is_valid_image_data(image_data):
                logging.error(f"API вернуло невалидные данные изображения. Первые 100 байт: {image_data[:100]}")
                return 'FAILURE', {"error": "API сгенерировало данные без стандартных сигнатур PNG/JPEG/WebP."}
            logging.info("Изображение от Gemini успешно сгенерировано.")
            return 'SUCCESS', {"image_data": image_data}
            
        elif text_response:
            logging.warning(f"Gemini не вернул изображение, но вернул текст: {text_response}")
            return 'FAILURE', {"error": f"Модель отказала в генерации: {text_response}"}
        else:
            logging.error("Gemini не вернул ни изображение, ни текст.")
            return 'FAILURE', {"error": "API не вернуло ни изображения, ни текста."}

    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        logging.error(f"Критическая ошибка в process_gemini_image_generation: {error_traceback}")
        return 'FAILURE', {"error": f"Ошибка при обращении к Gemini API: {str(e)}"}

async def handle_image_generation_command(message: types.Message):
    """
    Хендлер для команды 'нарисуй' (или аналогичной) с использованием Gemini.
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    
    # Логика извлечения промпта
    prompt = None
    if message.text.lower().startswith("нарисуй "):
        prompt = message.text[len("нарисуй "):].strip()
    elif message.text.lower().strip() == "нарисуй" and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    
    if not prompt:
        await message.reply("Что именно нарисовать? Напиши после команды или ответь на сообщение.")
        return

    processing_message = await message.reply("Рисую по вашему запросу... 🎨")
    
    # === ВЫЗЫВАЕМ ИСПРАВЛЕННУЮ ФУНКЦИЮ ===
    status, data = await process_gemini_image_generation(prompt)

    await processing_message.delete()
    
    if status == 'SUCCESS':
        await save_and_send_generated_image(message, data['image_data'])
    elif status == 'REFINED_PROMPT':
        # Если модель возвращает уточненный промпт, то это логика текстовой модели. 
        # Если используется gemini-2.0-flash с ['IMAGE'], это маловероятно, 
        # но если вдруг сработает, то можно запустить повторную генерацию или просто выдать ошибку.
        await message.reply(f"Модель предложила уточненный промпт, но не смогла сгенерировать изображение: {data['new_prompt']}")
    else:
        await message.reply(f"Не удалось нарисовать изображение. Причина: {data.get('error', 'Неизвестная ошибка.')}")

# ... (Остальные ваши функции: handle_pun_image_command, handle_redraw_command и т.д.)
