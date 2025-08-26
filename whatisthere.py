import os
import random
import logging
import requests
import re
from aiogram import types
from config import bot, API_TOKEN, model
# Импортируем новый единый список промптов
from prompts import PROMPTS_MEDIA

def get_custom_prompt(message: types.Message) -> str | None:
    """
    Извлекает кастомный промпт из сообщения, который идет после 'чотам'.
    """
    text = message.text or message.caption or ""
    if "чотам" in text.lower():
        # Используем re.split для регистронезависимого разделения, чтобы отделить команду от промпта
        parts = re.split(r'чотам', text, 1, re.IGNORECASE)
        if len(parts) > 1 and parts[1].strip():
            return parts[1].strip()
    return None

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
async def analyze_media(file_path: str, mime_type: str, custom_prompt: str | None = None) -> str:
    """Анализирует медиафайл и возвращает текстовое описание."""
    try:
        with open(file_path, "rb") as media_file:
            media_data = media_file.read()

        # Используем кастомный промпт если он есть, иначе случайный
        if custom_prompt:
            content_prompt = custom_prompt
        else:
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
            custom_prompt = get_custom_prompt(message)
            description = await analyze_media(file_name, mime_type, custom_prompt)
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
            custom_prompt = get_custom_prompt(message)
            description = await analyze_media(file_name, mime_type, custom_prompt)
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
            custom_prompt = get_custom_prompt(message)
            description = await analyze_media(file_name, mime_type, custom_prompt)
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
            custom_prompt = get_custom_prompt(message)
            description = await analyze_media(file_name, mime_type, custom_prompt)
            return True, description
        finally:
            if os.path.exists(file_name):
                os.remove(file_name)
    except Exception as e:
        logging.error(f"Ошибка при обработке GIF 'чотам': {e}")
        return False, "Ошибка при анализе гифки."

# ================== STICKER ==================
def extract_sticker_info(message: types.Message) -> tuple[str | None, str, str | None]:
    target_message = message.reply_to_message if message.reply_to_message else message
    if target_message.sticker:
        sticker = target_message.sticker
        file_id = sticker.file_id
        # Стикеры могут быть в формате webp или tgs (анимированные)
        if sticker.is_animated:
            file_name = f"sticker_{file_id}.tgs"
            mime_type = "application/x-tgsticker"
        else:
            file_name = f"sticker_{file_id}.webp"
            mime_type = "image/webp"
        return file_id, file_name, mime_type
    return None, "", None

async def process_sticker_whatisthere(message: types.Message) -> tuple[bool, str]:
    try:
        file_id, file_name, mime_type = extract_sticker_info(message)
        if not file_id:
            return False, "Ошибка: нет стикера для анализа."
        if not await download_file(file_id, file_name):
            return False, "Не удалось загрузить стикер."
        try:
            custom_prompt = get_custom_prompt(message)
            description = await analyze_media(file_name, mime_type, custom_prompt)
            return True, description
        finally:
            if os.path.exists(file_name):
                os.remove(file_name)
    except Exception as e:
        logging.error(f"Ошибка при обработке стикера 'чотам': {e}")
        return False, "Ошибка при анализе стикера."

# ================== TEXT ==================
async def process_text_whatisthere(message: types.Message) -> tuple[bool, str]:
    """
    Обработка текста по команде 'чотам'
    """
    try:
        # Текст для анализа должен быть в отвеченном сообщении
        if not (message.reply_to_message and message.reply_to_message.text):
            return False, "Для анализа текста ответьте на сообщение командой 'чотам'."
        
        text_to_analyze = message.reply_to_message.text
        custom_prompt = get_custom_prompt(message)
        
        # Выбираем промпт
        if custom_prompt:
            content_prompt = custom_prompt
        else:
            content_prompt = random.choice(PROMPTS_MEDIA)
        
        # Формируем запрос для анализа текста
        prompt = f"{content_prompt}\n\nТекст для анализа: {text_to_analyze}"
        
        response = model.generate_content(prompt)
        return True, response.text
        
    except Exception as e:
        logging.error(f"Ошибка при обработке текста 'чотам': {e}")
        return False, "Ошибка при анализе текста."

# ================== УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ==================
async def process_whatisthere_unified(message: types.Message) -> tuple[bool, str]:
    """
    Универсальная функция для обработки всех типов медиа и текста по команде 'чотам'
    """
    target_message = message.reply_to_message if message.reply_to_message else message
    
    # Определяем тип медиа и вызываем соответствующую функцию
    if target_message.audio or target_message.voice:
        return await process_audio_description(message)
    elif target_message.video:
        return await process_video_description(message)
    elif target_message.photo:
        return await process_image_whatisthere(message)
    elif target_message.animation:
        return await process_gif_whatisthere(message)
    elif target_message.sticker:
        return await process_sticker_whatisthere(message)
    # Проверяем, есть ли текст для анализа (в реплае или в самом сообщении, если нет реплая)
    elif target_message.text or (message.text and "чотам" in message.text.lower()):
        return await process_text_whatisthere(message)
    else:
        return False, "Не найдено контента для анализа."

def get_processing_message(message: types.Message) -> str:
    """
    Возвращает подходящее сообщение о процессе в зависимости от типа медиа
    """
    target_message = message.reply_to_message if message.reply_to_message else message
    
    if target_message.audio or target_message.voice:
        return "Слушою..."
    elif target_message.video:
        return "Сматрю..."
    elif target_message.photo:
        return "Рассматриваю ето художество..."
    elif target_message.animation:
        return "Да не дергайся ты..."
    elif target_message.sticker:
        return "Стикер-шмикер..."
    elif target_message.text or (message.text and "чотам" in message.text.lower()):
        return "Понаписали ебанарот..."
    else:
        return "Анализирую..."
