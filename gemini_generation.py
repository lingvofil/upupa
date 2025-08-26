import asyncio
import logging
import random
import base64
from io import BytesIO

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from PIL import Image

# Импортируем специальную модель для генерации изображений из конфига
from config import image_model, bot 
from prompts import actions

async def save_and_send_generated_image(message: types.Message, image_data: bytes, caption: str = None):
    """
    Обрабатывает и отправляет данные изображения пользователю как фото.
    Сначала пытается отправить "как есть", при ошибке - обрабатывает через Pillow.
    """
    try:
        # --- Попытка 1: Отправить необработанные данные напрямую ---
        logging.info("Попытка №1: отправка необработанных данных изображения...")
        raw_buffered_image = types.BufferedInputFile(image_data, filename="gemini_image_raw.png")
        await message.reply_photo(raw_buffered_image, caption=caption)
        logging.info("Необработанные данные успешно отправлены.")

    except TelegramBadRequest:
        logging.warning("Попытка №1 не удалась. Telegram не смог обработать изображение. Запускаю попытку №2 с обработкой через Pillow.")
        
        # --- Попытка 2: Обработка через Pillow как запасной вариант ---
        try:
            image = Image.open(BytesIO(image_data))
            
            output_buffer = BytesIO()
            image.save(output_buffer, 'PNG')
            output_buffer.seek(0)
            
            processed_buffered_image = types.BufferedInputFile(output_buffer.read(), filename="gemini_image_processed.png")
            await message.reply_photo(processed_buffered_image, caption=caption)
            logging.info("Обработанное через Pillow изображение успешно отправлено.")

        except Exception as pil_error:
            logging.error(f"Попытка №2 (Pillow) также не удалась: {pil_error}")
            logging.error(f"Первые 100 байт данных, которые не удалось распознать: {image_data[:100]}")
            await message.reply("Не удалось обработать сгенерированное изображение. API вернуло данные в неизвестном формате.")
            
    except Exception as e:
        logging.error(f"Критическая ошибка при отправке фото от Gemini: {e}")
        await message.reply("Не смог отправить картинку, что-то пошло не так.")


async def process_gemini_generation(prompt: str):
    """
    Основная логика генерации изображения через Gemini API.
    Возвращает статус и результат.
    Статусы: 'SUCCESS', 'REFINED_PROMPT', 'FAILURE'
    """
    try:
        logging.info(f"Запрос к Gemini с промптом: {prompt}")
        
        response = await asyncio.to_thread(
            image_model.generate_content,
            contents=prompt,
            generation_config={
                'response_modalities': ['TEXT', 'IMAGE']
            }
        )
        
        image_data = None
        text_response = ""

        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                image_data = base64.b64decode(part.inline_data.data)
            elif part.text:
                text_response += part.text.strip()
        
        if image_data:
            logging.info("Изображение от Gemini успешно сгенерировано.")
            return 'SUCCESS', {"image_data": image_data, "caption": text_response}
        elif text_response:
            logging.warning(f"Gemini не вернул изображение, но вернул текст (возможно, уточненный промпт): {text_response}")
            return 'REFINED_PROMPT', {"new_prompt": text_response}
        else:
            logging.error("Gemini не вернул ни изображение, ни текст.")
            return 'FAILURE', {"error": "API не вернуло ни изображения, ни текста."}

    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        logging.error(f"Критическая ошибка в process_gemini_generation: {error_traceback}")
        return 'FAILURE', {"error": f"Произошла ошибка при обращении к Gemini API: {repr(e)}"}


async def handle_gemini_generation_command(message: types.Message):
    """
    Обработчик команды 'сгенерируй' с логикой повторной попытки.
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))

    prompt = None
    if message.text.lower().startswith("сгенерируй "):
        prompt = message.text[len("сгенерируй "):].strip()
    elif message.text.lower().strip() == "сгенерируй" and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption

    if not prompt:
        await message.reply("Что именно сгенерировать? Напиши после команды или ответь на сообщение.")
        return

    processing_message = await message.reply("Думаю над вашим запросом... 🤖")
    
    # --- Первая попытка ---
    status, data = await process_gemini_generation(prompt)
    
    if status == 'SUCCESS':
        await processing_message.delete()
        await save_and_send_generated_image(message, data['image_data'], caption=data.get('caption'))
        return

    if status == 'REFINED_PROMPT':
        await processing_message.edit_text("Запрос был слишком общим. Уточняю и пробую снова...")
        new_prompt = data['new_prompt']
        
        # --- Вторая попытка с уточненным промптом ---
        status, data = await process_gemini_generation(new_prompt)
        
        if status == 'SUCCESS':
            await processing_message.delete()
            await save_and_send_generated_image(message, data['image_data'], caption=data.get('caption'))
            return

    # --- Обработка ошибки (после первой или второй попытки) ---
    await processing_message.delete()
    error_text = f"Не удалось сгенерировать изображение. Причина: {data.get('error', 'Неизвестная ошибка.')}"
    await message.reply(error_text)
