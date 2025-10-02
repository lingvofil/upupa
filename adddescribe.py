import base64
import logging
import os
import textwrap
import requests
import random
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from aiogram import types
from aiogram.types import FSInputFile

from config import API_TOKEN, model, bot 
from prompts import PROMPT_DESCRIBE, SPECIAL_PROMPT, actions

# =============================================================================
# НОВАЯ ФУНКЦИЯ-ОБРАБОТЧИК
# =============================================================================
async def handle_add_text_command(message: types.Message):
    """
    Полностью обрабатывает команду "добавь": находит фото, генерирует текст
    или использует пользовательский текст, накладывает его на изображение,
    отправляет результат и обрабатывает ошибки.
    """
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))

        photo = await get_photo_from_message(message)
        if not photo:
            await message.reply("Изображение для обработки не найдено.")
            return

        # Извлекаем пользовательский текст
        user_text = extract_user_text(message)
        
        image_bytes = await download_telegram_image(bot, photo)
        
        # Если есть пользовательский текст, используем его, иначе генерируем
        if user_text:
            logging.info(f"Используем пользовательский текст: {user_text}")
            generated_text = user_text
        else:
            logging.info("Генерируем текст через AI")
            generated_text = await process_image(image_bytes)
        
        modified_image_path = overlay_text_on_image(image_bytes, generated_text)
        
        photo_file = FSInputFile(modified_image_path)
        await message.reply_photo(photo_file)

    except Exception as e:
        logging.error(f"Ошибка в handle_add_text_command: {e}", exc_info=True)
        await message.reply(f"Произошла непредвиденная ошибка при обработке изображения.")
    finally:
        if os.path.exists("modified_image.jpg"):
            try:
                os.remove("modified_image.jpg")
            except OSError as e:
                logging.error(f"Не удалось удалить временный файл modified_image.jpg: {e}")

def extract_user_text(message: types.Message) -> str | None:
    """
    Извлекает текст после команды "добавь".
    Возвращает None, если текст не найден.
    """
    try:
        # Случай 1: текст в caption (фото с подписью)
        if message.caption:
            text = message.caption.lower()
            if "добавь" in text:
                # Находим позицию слова "добавь" и берем все после него
                idx = text.find("добавь")
                user_text = message.caption[idx + 6:].strip()
                return user_text if user_text else None
        
        # Случай 2: текст в message.text (ответ на фото)
        elif message.text:
            text = message.text.lower()
            if "добавь" in text:
                idx = text.find("добавь")
                user_text = message.text[idx + 6:].strip()
                return user_text if user_text else None
        
        return None
        
    except Exception as e:
        logging.error(f"Ошибка в extract_user_text: {e}", exc_info=True)
        return None

# =============================================================================
# ФУНКЦИИ ДЛЯ КОМАНДЫ "ОПИШИ"
# =============================================================================
async def process_image_description(bot, message: types.Message) -> tuple[bool, str]:
    """
    Основная функция для обработки команды "опиши"
    """
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
        
        photo = await get_photo_from_message(message)
        if not photo:
            return False, "Изображение для описания не найдено."
        
        image_data = await download_image(bot, photo.file_id)
        if not image_data:
            return False, "Не удалось загрузить изображение."
        
        success, description = await generate_image_description(image_data)
        
        if success:
            return True, description
        else:
            return False, description
            
    except Exception as e:
        logging.error(f"Ошибка в process_image_description: {e}", exc_info=True)
        return False, "Произошла ошибка при обработке изображения."

async def download_image(bot, file_id: str) -> bytes | None:
    """
    Загружает изображение по file_id
    """
    try:
        file = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{API_TOKEN}/{file.file_path}"
        logging.info(f"Загружаем изображение с URL: {file_url}")
        
        response = requests.get(file_url)
        if response.status_code == 200:
            return response.content
        else:
            logging.error(f"Ошибка загрузки изображения: статус {response.status_code}")
            return None
            
    except Exception as e:
        logging.error(f"Ошибка в download_image: {e}", exc_info=True)
        return None

async def generate_image_description(image_data: bytes) -> tuple[bool, str]:
    """
    Генерирует описание изображения с помощью AI модели
    """
    try:
        response = model.generate_content([
            PROMPT_DESCRIBE,
            {"mime_type": "image/jpeg", "data": image_data}
        ])
        
        description = response.text
        logging.info(f"Сгенерированное описание: {description}")
        return True, description
        
    except Exception as e:
        logging.error(f"Ошибка генерации описания: {e}", exc_info=True)
        return False, f"Ошибка генерации описания: {str(e)}"

async def extract_image_info(message: types.Message) -> str | None:
    """
    Извлекает информацию об изображении из сообщения
    """
    try:
        if message.photo:
            photo = message.photo[-1]
            return photo.file_id
        elif message.reply_to_message:
            if message.reply_to_message.photo:
                photo = message.reply_to_message.photo[-1]
                return photo.file_id
            elif message.reply_to_message.document:
                doc = message.reply_to_message.document
                if doc.mime_type and doc.mime_type.startswith('image/'):
                    return doc.file_id
        return None
        
    except Exception as e:
        logging.error(f"Ошибка в extract_image_info: {e}", exc_info=True)
        return None

# =============================================================================
# ОБЩИЕ ФУНКЦИИ (используются и для "добавь" и для "опиши")
# =============================================================================
async def get_photo_from_message(message: types.Message):
    if message.photo:
        return message.photo[-1]
    elif message.reply_to_message and (message.reply_to_message.photo or message.reply_to_message.document):
        if message.reply_to_message.photo:
            return message.reply_to_message.photo[-1]
        return message.reply_to_message.document
    return None

async def download_telegram_image(bot, photo):
    file = await bot.get_file(photo.file_id)
    file_url = f"https://api.telegram.org/file/bot{API_TOKEN}/{file.file_path}"
    logging.info(f"Загружаем изображение с URL: {file_url}")
    response = requests.get(file_url)
    if response.status_code != 200:
        raise Exception("Не удалось загрузить изображение.")
    return response.content

async def process_image(image_bytes: bytes) -> str:
    """
    Обрабатывает изображение и генерирует текст для команды "добавь".
    """
    try:
        response = model.generate_content([
            SPECIAL_PROMPT,
            {"mime_type": "image/jpeg", "data": image_bytes}
        ])
        generated_text = response.text
        logging.info(f"Сгенерированный текст: {generated_text}")
        return generated_text
    except Exception as e:
        logging.error(f"Ошибка обработки изображения: {str(e)}", exc_info=True)
        raise RuntimeError(f"Ошибка генерации текста: {str(e)}") from e

def get_text_size(font, text):
    bbox = font.getbbox(text)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return width, height

def overlay_text_on_image(image_bytes: bytes, text: str) -> str:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    font_size = 48
    font = ImageFont.truetype(font_path, font_size)
    max_width = image.width - 20
    sample_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzАБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдежзийклмнопрстуфхцчшщъыьэюя"
    total_width = sum(get_text_size(font, char)[0] for char in sample_chars)
    avg_char_width = total_width / len(sample_chars)
    max_chars_per_line = int(max_width // avg_char_width)
    lines = textwrap.wrap(text, width=max_chars_per_line)
    _, line_height = get_text_size(font, "A")
    text_block_height = line_height * len(lines)
    margin_bottom = 60
    y = image.height - text_block_height - margin_bottom
    rectangle = Image.new('RGBA', (image.width, text_block_height + 40), (0, 0, 0, 128))
    image.paste(rectangle, (0, y - 5), rectangle)
    for line in lines:
        text_width, _ = get_text_size(font, line)
        x = (image.width - text_width) / 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height + 10
    output_path = "modified_image.jpg"
    image.save(output_path)
    return output_path
