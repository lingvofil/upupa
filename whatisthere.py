import os
import random
import logging
import requests
import subprocess
from aiogram import types
from Config import bot, API_TOKEN, model
# Импортируем новый единый список промптов
from Prompts import PROMPTS_MEDIA

# Общая функция для скачивания файлов
async def download_file(file_id: str, file_name: str) -> bool:
    """
    Загружает файл из Telegram.
    Returns: bool: True если загрузка успешна, False в противном случае
    """
    try:
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path
        file_url = f"https://api.telegram.org/file/bot{API_TOKEN}/{file_path}"
        
        response = requests.get(file_url)
        response.raise_for_status() # Вызовет ошибку при плохом ответе
            
        with open(file_name, "wb") as f:
            f.write(response.content)
        return True
        
    except Exception as e:
        logging.error(f"Ошибка при загрузке файла {file_id}: {e}")
        return False

# Общая функция анализа для всех медиа
async def analyze_media(file_path: str, mime_type: str) -> str:
    """Анализирует медиафайл и возвращает текстовое описание."""
    try:
        with open(file_path, "rb") as media_file:
            media_data = media_file.read()

        # Используем единый список промптов
        content_prompt = random.choice(PROMPTS_MEDIA)
        
        contents = [
            {"mime_type": mime_type, "data": media_data},
            content_prompt
        ]
        response = model.generate_content(contents)
        return response.text
    except Exception as e:
        logging.error(f"Ошибка при анализе медиа ({mime_type}): {e}")
        return "Нихуя не понял, давай еще раз."

# ================== AUDIO ==================
async def extract_audio_info(message: types.Message) -> tuple[str | None, str | None, str | None]:
    target_message = message.reply_to_message if message.reply_to_message else message
    if target_message.audio:
        return (target_message.audio.file_id, "audio/mpeg", f"audio_{target_message.audio.file_id}.mp3")
    elif target_message.voice:
        return (target_message.voice.file_id, "audio/ogg", f"voice_{target_message.voice.file_id}.ogg")
    return None, None, None

async def process_audio_description(message: types.Message) -> tuple[bool, str]:
    try:
        file_id, mime_type, file_name = await extract_audio_info(message)
        if not all([file_id, mime_type, file_name]):
            return False, "Ошибка: нет аудиофайла для анализа."
        if not await download_file(file_id, file_name):
            return False, "Не удалось загрузить аудио."
        try:
            description = await analyze_media(file_name, mime_type)
            return True, description
        finally:
            if os.path.exists(file_name):
                os.remove(file_name)
    except Exception as e:
        logging.error(f"Ошибка при обработке аудио: {e}")
        return False, "Ошибка при анализе аудио."

# ================== VIDEO ==================
async def extract_video_info(message: types.Message) -> tuple[str | None, str, str | None]:
    target_message = message.reply_to_message if message.reply_to_message else message
    if target_message.video:
        file_id = target_message.video.file_id
        file_name = f"video_{file_id}.mp4"
        mime_type = target_message.video.mime_type or "video/mp4"
        return file_id, file_name, mime_type
    return None, "", None

async def process_video_description(message: types.Message) -> tuple[bool, str]:
    try:
        file_id, file_name, mime_type = await extract_video_info(message)
        if not file_id:
            return False, "Ошибка: нет видеофайла для анализа."
        if not await download_file(file_id, file_name):
            return False, "Не удалось загрузить видео."
        try:
            description = await analyze_media(file_name, mime_type)
            return True, description
        finally:
            if os.path.exists(file_name):
                os.remove(file_name)
    except Exception as e:
        logging.error(f"Ошибка при обработке видео: {e}")
        return False, "Ошибка при анализе видео."

# ================== IMAGE ==================
def extract_image_info(message: types.Message) -> tuple[str | None, str, str | None]:
    target_message = message.reply_to_message if message.reply_to_message else message
    if target_message.photo:
        photo = target_message.photo[-1]
        file_id = photo.file_id
        file_name = f"photo_{file_id}.jpg" 
        mime_type = "image/jpeg" 
        return file_id, file_name, mime_type
    return None, "", None

async def process_image_whatisthere(message: types.Message) -> tuple[bool, str]:
    try:
        file_id, file_name, mime_type = extract_image_info(message)
        if not file_id:
            return False, "Ошибка: нет картинки для анализа."
        if not await download_file(file_id, file_name):
            return False, "Не удалось загрузить картинку."
        try:
            description = await analyze_media(file_name, mime_type)
            return True, description
        finally:
            if os.path.exists(file_name):
                os.remove(file_name)
    except Exception as e:
        logging.error(f"Ошибка при обработке изображения 'чотам': {e}")
        return False, "Ошибка при анализе картинки."

# ================== GIF ==================
def extract_gif_info(message: types.Message) -> tuple[str | None, str, str | None]:
    target_message = message.reply_to_message if message.reply_to_message else message
    if target_message.animation:
        animation = target_message.animation
        file_id = animation.file_id
        file_name = f"gif_{file_id}.mp4" 
        mime_type = animation.mime_type or "image/gif"
        return file_id, file_name, mime_type
    return None, "", None

async def process_gif_whatisthere(message: types.Message) -> tuple[bool, str]:
    try:
        file_id, file_name, mime_type = extract_gif_info(message)
        if not file_id:
            return False, "Ошибка: нет гифки для анализа."
        if not await download_file(file_id, file_name):
            return False, "Не удалось загрузить гифку."
        try:
            description = await analyze_media(file_name, mime_type)
            return True, description
        finally:
            if os.path.exists(file_name):
                os.remove(file_name)
    except Exception as e:
        logging.error(f"Ошибка при обработке GIF 'чотам': {e}")
        return False, "Ошибка при анализе гифки."
