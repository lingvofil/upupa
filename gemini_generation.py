import asyncio
import logging
import random
import base64
import os
import textwrap
from io import BytesIO

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile
from PIL import Image, ImageDraw, ImageFont

# Импортируем модели и другие утилиты
from config import image_model, model, bot 
from prompts import actions
from adddescribe import download_telegram_image

def is_valid_image_data(data: bytes) -> bool:
    """Проверяет, начинаются ли байты с сигнатур известных форматов изображений."""
    if data.startswith(b'\x89PNG') or data.startswith(b'\xff\xd8') or data.startswith(b'RIFF'):
        return True
    return False

async def save_and_send_generated_image(message: types.Message, image_data: bytes, caption: str = None):
    """Отправляет сгенерированное изображение, пытаясь сначала напрямую, потом через обработку."""
    try:
        logging.info("Попытка №1: отправка необработанных данных изображения...")
        raw_buffered_image = types.BufferedInputFile(image_data, filename="gemini_image_raw.png")
        await message.reply_photo(raw_buffered_image, caption=caption)
        logging.info("Необработанные данные успешно отправлены.")
    except TelegramBadRequest:
        logging.warning("Попытка №1 не удалась. Запускаю Pillow.")
        try:
            image = Image.open(BytesIO(image_data))
            output_buffer = BytesIO()
            image.save(output_buffer, 'PNG')
            output_buffer.seek(0)
            processed_buffered_image = types.BufferedInputFile(output_buffer.read(), filename="gemini_image_processed.png")
            await message.reply_photo(processed_buffered_image, caption=caption)
            logging.info("Обработанное через Pillow изображение успешно отправлено.")
        except Exception as pil_error:
            logging.error(f"Pillow не смог обработать: {pil_error}")
            await message.reply("API вернуло данные, которые не являются изображением.")

async def process_gemini_generation(prompt: str):
    """Основная логика генерации изображения через Gemini API."""
    try:
        logging.info(f"Запрос к Gemini с промптом: {prompt}")
        response = await asyncio.to_thread(
            image_model.generate_content,
            contents=prompt,
            generation_config={'response_modalities': ['TEXT', 'IMAGE']}
        )
        image_data, text_response = None, ""
        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                image_data = base64.b64decode(part.inline_data.data)
            elif hasattr(part, "text") and part.text:
                text_response += part.text.strip()
        
        if image_data:
            if not is_valid_image_data(image_data):
                logging.error(f"API вернуло невалидные данные изображения. Первые 100 байт: {image_data[:100]}")
                return 'FAILURE', {"error": "API сгенерировало данные без стандартных сигнатур PNG/JPEG/WebP."}
            logging.info("Изображение от Gemini успешно сгенерировано.")
            return 'SUCCESS', {"image_data": image_data, "caption": text_response}
        elif text_response:
            logging.warning(f"Gemini не вернул изображение, но вернул текст: {text_response}")
            return 'REFINED_PROMPT', {"new_prompt": text_response}
        else:
            logging.error("Gemini не вернул ни изображение, ни текст.")
            return 'FAILURE', {"error": "API не вернуло ни изображения, ни текста."}
    except Exception as e:
        import traceback
        logging.error(f"Критическая ошибка в process_gemini_generation: {traceback.format_exc()}")
        return 'FAILURE', {"error": f"Ошибка при обращении к Gemini API: {repr(e)}"}

async def handle_draw_command(message: types.Message):
    """Обработчик команды 'нарисуй'."""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompt = None
    if message.text.lower().startswith("нарисуй "):
        prompt = message.text[len("нарисуй "):].strip()
    elif message.text.lower().strip() == "нарисуй" and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    if not prompt:
        await message.reply("Что именно нарисовать? Напиши после команды или ответь на сообщение.")
        return
    
    processing_message = await message.reply("Думаю над вашим запросом... 🤖")
    status, data = await process_gemini_generation(prompt)

    if status == 'SUCCESS':
        await processing_message.delete()
        await save_and_send_generated_image(message, data['image_data'], caption=data.get('caption'))
        return
    
    if status == 'REFINED_PROMPT':
        await processing_message.edit_text("Запрос был слишком общим. Уточняю и пробую снова...")
        new_prompt = data['new_prompt']
        status, data = await process_gemini_generation(new_prompt)
        if status == 'SUCCESS':
            await processing_message.delete()
            await save_and_send_generated_image(message, data['image_data'], caption=data.get('caption'))
            return
            
    await processing_message.delete()
    await message.reply(f"Не удалось нарисовать. Причина: {data.get('error', 'Неизвестная ошибка.')}")

async def handle_redraw_command(message: types.Message):
    """Обработчик команды 'перерисуй' с использованием Gemini."""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Анализирую вашу мазню...")
    try:
        photo = None
        if message.photo: photo = message.photo[-1]
        elif message.document: photo = message.document
        elif message.reply_to_message and (message.reply_to_message.photo or message.reply_to_message.document):
            photo = message.reply_to_message.photo[-1] if message.reply_to_message.photo else message.reply_to_message.document
        
        if not photo:
            await processing_msg.edit_text("Изображение для перерисовки не найдено.")
            return
            
        image_bytes = await download_telegram_image(bot, photo)
        detailed_prompt = "Опиши детально все, что видишь на этом изображении. Укажи: основные объекты, цвета, стиль, фон, детали. Опиши максимально подробно для воссоздания изображения, должен получиться очень плохо и криво нарисованный рисунок карандашом, как будто рисовал трехлетний ребенок. Весь текст должен вмещаться в один абзац, не более 100 слов"
        
        def sync_describe():
            return model.generate_content([detailed_prompt, {"mime_type": "image/jpeg", "data": image_bytes}]).text.strip()
        
        description = await asyncio.to_thread(sync_describe)
        logging.info(f"Gemini-описание для перерисовки: {description}")
        
        await processing_msg.edit_text("Перерисовываю как могу...")
        status, data = await process_gemini_generation(description)
        
        if status == 'SUCCESS':
            await processing_msg.delete()
            await save_and_send_generated_image(message, data['image_data'], caption="Вот что получилось:")
        else:
            error_text = f"Ошибка, блин: {data.get('error', 'Неизвестная ошибка.')}"
            await processing_msg.edit_text(error_text)
    
    except Exception as e:
        logging.error(f"Ошибка в handle_redraw_command: {str(e)}", exc_info=True)
        await processing_msg.edit_text(f"Произошла непредвиденная ошибка: {str(e)[:200]}")

# --- Функции для каламбура ---
def _get_text_size(font, text):
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        return font.getsize(text)

def _overlay_text_on_image(image_bytes: bytes, text: str) -> str:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not os.path.exists(font_path): font_path = "arial.ttf"
    font = ImageFont.truetype(font_path, 48)
    max_width = image.width - 40
    sample_chars = "абвгдежзийклмнопрстуфхцчшщъыьэюя"
    avg_char_width = sum(_get_text_size(font, char)[0] for char in sample_chars) / len(sample_chars)
    max_chars_per_line = int(max_width // avg_char_width) if avg_char_width > 0 else 20
    lines = textwrap.wrap(text, width=max_chars_per_line)
    _, line_height = _get_text_size(font, "A")
    text_block_height = (line_height + 5) * len(lines)
    y = image.height - text_block_height - 60
    rectangle = Image.new('RGBA', (image.width, text_block_height + 40), (0, 0, 0, 128))
    image.paste(rectangle, (0, y - 20), rectangle)
    current_y = y - 10
    for line in lines:
        text_width, _ = _get_text_size(font, line)
        x = (image.width - text_width) / 2
        draw.text((x, current_y), line, font=font, fill="white", stroke_width=1, stroke_fill="black")
        current_y += line_height + 5
    output_path = "modified_pun_image.jpg"
    image.save(output_path)
    return output_path

async def handle_pun_image_command(message: types.Message):
    """Обрабатывает команду 'скаламбурь' с помощью Gemini."""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Сочиняю каламбур...")
    pun_prompt = "составь каламбурное сочетание слов в одном слове. должно быть пересечение конца первого слова с началом второго. Совпадать должны как минимум две буквы. Не комментируй генерацию. Ответ дай строго в формате: 'слово1+слово2 = итоговоеслово' Например: 'манго+голубь = манголубь'"
    modified_image_path = None
    try:
        def sync_call(): return model.generate_content(pun_prompt).text.strip()
        pun_word = await asyncio.to_thread(sync_call)
        logging.info(f"Сгенерированный каламбур (raw): {pun_word}")
        parts = pun_word.split('=')
        original_words, final_word = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (pun_word, pun_word)
        logging.info(f"Попытка сочетания слов: {original_words} => {final_word}")
        
        await processing_msg.edit_text(f"Рисую каламбур: {final_word}")
        
        # Генерация изображения для каламбура
        status, data = await process_gemini_generation(original_words)
        
        # Если первая попытка неудачна, но есть уточнение, пробуем еще раз
        if status == 'REFINED_PROMPT':
             await processing_msg.edit_text(f"Уточняю запрос для каламбура и рисую: {data['new_prompt']}")
             status, data = await process_gemini_generation(data['new_prompt'])

        if status == 'SUCCESS':
            image_data = data['image_data']
            modified_image_path = _overlay_text_on_image(image_data, final_word)
            await message.reply_photo(FSInputFile(modified_image_path))
            await processing_msg.delete()
        else:
            await processing_msg.edit_text(f"Ошибка при генерации изображения: {data.get('error', 'Неизвестная ошибка.')}")
    except Exception as e:
        logging.error(f"Ошибка в handle_pun_image_command: {str(e)}", exc_info=True)
        await processing_msg.edit_text(f"Произошла непредвиденная ошибка: {str(e)[:200]}")
    finally:
        if modified_image_path and os.path.exists(modified_image_path):
            os.remove(modified_image_path)
