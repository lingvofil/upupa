import os
import random
import logging
import requests
import re
from aiogram import types
from config import bot, API_TOKEN, model
# Импортируем новый единый список промптов
from prompts import PROMPTS_MEDIA
# НОВОЕ: Снова импортируем BeautifulSoup
from bs4 import BeautifulSoup

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

# Общая функция анализа для медиа (принимает путь ИЛИ байты)
# === ИЗМЕНЕНИЕ 1: Добавили аргумент chat_id ===
async def analyze_media_bytes(media_source: str | bytes, mime_type: str, custom_prompt: str | None = None, chat_id: int | str | None = None) -> str:
    """
    Анализирует медиафайл (из пути или байтов) и возвращает текстовое описание.
    """
    try:
        media_data = None
        if isinstance(media_source, str): # Если это путь к файлу
            with open(media_source, "rb") as media_file:
                media_data = media_file.read()
        elif isinstance(media_source, bytes): # Если это уже байты
            media_data = media_source
        else:
            raise ValueError("Неверный тип media_source: нужен str (путь) или bytes")

        # Используем кастомный промпт если он есть, иначе случайный
        if custom_prompt:
            content_prompt = f"{custom_prompt}, не более 80 слов"
        else:
            base_prompt = random.choice(PROMPTS_MEDIA)
            content_prompt = f"{base_prompt}, не более 80 слов"
        
        contents = [
            {"mime_type": mime_type, "data": media_data},
            content_prompt
        ]
        # === ИЗМЕНЕНИЕ 2: Передаем chat_id в модель ===
        response = model.generate_content(contents, chat_id=chat_id)
        return response.text
    except Exception as e:
        logging.error(f"Ошибка при анализе медиа байтов ({mime_type}): {e}")
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
            # Передаем chat_id
            description = await analyze_media_bytes(file_name, mime_type, custom_prompt, chat_id=message.chat.id)
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
            # Передаем chat_id
            description = await analyze_media_bytes(file_name, mime_type, custom_prompt, chat_id=message.chat.id)
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
            # Передаем chat_id
            description = await analyze_media_bytes(file_name, mime_type, custom_prompt, chat_id=message.chat.id)
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
            # Передаем chat_id
            description = await analyze_media_bytes(file_name, mime_type, custom_prompt, chat_id=message.chat.id)
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
            # Передаем chat_id
            description = await analyze_media_bytes(file_name, mime_type, custom_prompt, chat_id=message.chat.id)
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
        if not (message.reply_to_message and message.reply_to_message.text):
            return False, "Для анализа текста ответьте на сообщение командой 'чотам'."
        
        text_to_analyze = message.reply_to_message.text
        custom_prompt = get_custom_prompt(message)
        
        if custom_prompt:
            content_prompt = f"{custom_prompt}, не более 80 слов"
        else:
            base_prompt = random.choice(PROMPTS_MEDIA)
            content_prompt = f"{base_prompt}, не более 80 слов"
        
        prompt = f"{content_prompt}\n\nТекст для анализа: {text_to_analyze}"
        
        # === ИЗМЕНЕНИЕ 3: Передаем chat_id ===
        response = model.generate_content(prompt, chat_id=message.chat.id)
        return True, response.text
        
    except Exception as e:
        logging.error(f"Ошибка при обработке текста 'чотам': {e}")
        return False, "Ошибка при анализе текста."

# ================== ИСПРАВЛЕНО: URL (Возврат к BS4) ==================
def extract_url_from_message(message: types.Message) -> str | None:
    """Ищет URL в тексте сообщения или в его entities."""
    text = message.text or message.caption or ""
    if not text:
        return None
    
    # Сначала проверяем entities (более надежно)
    if message.entities:
        for entity in message.entities:
            if entity.type == 'url':
                # Исправление 'AttributeError'
                return text[entity.offset : entity.offset + entity.length]
                
    if message.caption_entities:
         for entity in message.caption_entities:
            if entity.type == 'url':
                # Исправление 'AttributeError'
                return text[entity.offset : entity.offset + entity.length]
                
    # Если entities нет, ищем простым regex
    match = re.search(r'https?://[^\s]+', text)
    if match:
        return match.group(0)
    return None

async def process_url_whatisthere(message: types.Message, url: str) -> tuple[bool, str]:
    """Обрабатывает контент по URL (ручной метод, т.к. url_context недоступен)"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'} 
        response = requests.get(url, timeout=10, allow_redirects=True, headers=headers)
        response.raise_for_status()
        
        content_type = response.headers.get('Content-Type', '').split(';')[0].strip()
        custom_prompt = get_custom_prompt(message)

        logging.info(f"URL: {url}, Content-Type: {content_type}")

        # Вариант 1: Медиа (Аудио, Видео, Изображение)
        if content_type.startswith(('audio/', 'video/', 'image/')):
            logging.info("Тип: Аудио/Видео/Изображение. Загружаю байты...")
            media_data = response.content
            # Передаем chat_id
            description = await analyze_media_bytes(media_data, content_type, custom_prompt, chat_id=message.chat.id)
            return True, description
            
        # Вариант 2: HTML или обычный текст
        elif content_type.startswith(('text/html', 'text/plain')):
            logging.info("Тип: HTML/Текст. Парсинг BeautifulSoup...")
            # Используем .content вместо .text для правильной обработки кодировки
            soup = BeautifulSoup(response.content, 'html.parser')
            text_to_analyze = soup.get_text(separator=' ', strip=True)

            if len(text_to_analyze) > 4000:
                text_to_analyze = text_to_analyze[:4000] + "..."
            
            if not text_to_analyze.strip():
                 return False, "Не смог извлечь текст с этой страницы (возможно, это JavaScript-сайт)."

            # Логика анализа текста (как в process_text_whatisthere)
            if custom_prompt:
                content_prompt = f"{custom_prompt}, не более 80 слов"
            else:
                base_prompt = random.choice(PROMPTS_MEDIA)
                content_prompt = f"{base_prompt}, не более 80 слов"
                
            prompt = f"{content_prompt}\n\nТекст для анализа (взято с сайта {url}): {text_to_analyze}"
            
            # === ИЗМЕНЕНИЕ 4: Передаем chat_id ===
            gen_response = model.generate_content(prompt, chat_id=message.chat.id)
            return True, gen_response.text

        # Вариант 3: Непонятный тип
        else:
            logging.warning(f"Неподдерживаемый/неопределенный тип контента по URL: {content_type}")
            return False, f"Не могу разобрать этот тип контента: {content_type} (URL: {url})"
            
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка при загрузке URL {url}: {e}")
        return False, "Не удалось загрузить контент по ссылке."
    except Exception as e:
        # Добавляем exc_info=True для полного трейсбека в логах
        logging.error(f"Ошибка при обработке URL 'чотам': {e}", exc_info=True)
        return False, "Ошибка при анализе ссылки."

# ================== УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ==================
async def process_whatisthere_unified(message: types.Message) -> tuple[bool, str]:
    """
    Универсальная функция для обработки всех типов медиа, текста и URL по команде 'чотам'
    """
    target_message = message.reply_to_message if message.reply_to_message else message
    
    # В первую очередь ищем URL
    # Проверяем и в реплае и в самом сообщении
    url = extract_url_from_message(target_message)
    if not url:
         # Если в реплае нет, ищем в самом триггер-сообщении
         if not message.reply_to_message:
            url = extract_url_from_message(message)

    if url:
        return await process_url_whatisthere(message, url)
        
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
    elif target_message.text:
        if message.reply_to_message:
             return await process_text_whatisthere(message)
        else:
             return False, "Для анализа текста ответьте на сообщение."
    else:
        return False, "Не найдено контента для анализа."

def get_processing_message(message: types.Message) -> str:
    """
    Возвращает подходящее сообщение о процессе в зависимости от типа медиа
    """
    target_message = message.reply_to_message if message.reply_to_message else message
    
    # Проверка на URL
    url = extract_url_from_message(target_message)
    if not url:
        if not message.reply_to_message:
            url = extract_url_from_message(message)

    if url:
        return "Лезю по ссылке..."
        
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
    elif target_message.text:
        return "Понаписали ебанарот..."
    else:
        return "Анализирую..."
