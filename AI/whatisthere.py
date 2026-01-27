#whatisthere.py

import os
import random
import logging
import requests
import re
import io
from aiogram import types
from config import bot, API_TOKEN, model, ROBOTICS_MODEL, groq_ai, gigachat_model, chat_settings
from prompts import PROMPTS_MEDIA
from bs4 import BeautifulSoup
import google.generativeai as genai

def get_active_model(chat_id: str) -> str:
    """Возвращает активную модель для чата"""
    settings = chat_settings.get(str(chat_id), {})
    active_model = settings.get("active_model", "gemini")
    
    # Режим истории не подходит для анализа медиа
    if active_model == "history":
        active_model = "gemini"
    
    return active_model

def get_custom_prompt(message: types.Message) -> str | None:
    """ Извлекает кастомный промпт из сообщения, который идет после 'чотам'. """
    text = message.text or message.caption or ""
    if "чотам" in text.lower():
        parts = re.split(r'чотам', text, 1, re.IGNORECASE)
        if len(parts) > 1 and parts[1].strip():
            return parts[1].strip()
    return None

# Общая функция для скачивания файлов
async def download_file(file_id: str, file_name: str) -> bool:
    """ Загружает файл из Telegram. Returns: bool: True если загрузка успешна, False в противном случае """
    try:
        file_info = await bot.get_file(file_id)
        file_path = file_info.file_path
        file_url = f"https://api.telegram.org/file/bot{API_TOKEN}/{file_path}"
        response = requests.get(file_url)
        response.raise_for_status() 
        with open(file_name, "wb") as f:
            f.write(response.content)
        return True
    except Exception as e:
        logging.error(f"Ошибка при загрузке файла {file_id}: {e}")
        return False

# Общая функция анализа для медиа (принимает путь ИЛИ байты)
async def analyze_media_bytes(media_source: str | bytes, mime_type: str, custom_prompt: str | None = None, chat_id: int | str | None = None) -> str:
    """ Анализирует медиафайл (из пути или байтов) и возвращает текстовое описание. """
    try:
        media_data = None
        temp_file_name = "media_input.tmp"
        
        if isinstance(media_source, str):
            with open(media_source, "rb") as media_file:
                media_data = media_file.read()
            temp_file_name = os.path.basename(media_source)
        elif isinstance(media_source, bytes):
            media_data = media_source
        else:
            raise ValueError("Неверный тип media_source: нужен str (путь) или bytes")

        # Используем кастомный промпт если он есть, иначе случайный
        if custom_prompt:
            content_prompt = f"{custom_prompt}, не более 80 слов"
        else:
            base_prompt = random.choice(PROMPTS_MEDIA)
            content_prompt = f"{base_prompt}, не более 80 слов"

        # Определяем активную модель
        active_model = get_active_model(str(chat_id)) if chat_id else "gemini"
        logging.info(f"analyze_media_bytes: используется модель {active_model} для {mime_type}")

        # === ИЗОБРАЖЕНИЯ ===
        if mime_type.startswith('image/') or mime_type == "image/webp":
            if active_model == "groq":
                try:
                    logging.info(f"Использую Groq с {groq_ai.vision_model} для {mime_type}")
                    return groq_ai.analyze_image(media_data, content_prompt)
                except Exception as ge:
                    logging.error(f"Groq Vision failed: {ge}. Falling back to Gemini.")
            elif active_model == "gigachat":
                try:
                    logging.info(f"Использую GigaChat для {mime_type}")
                    contents = [
                        {"mime_type": mime_type, "data": media_data},
                        content_prompt
                    ]
                    response = gigachat_model.generate_content(contents, chat_id=int(chat_id))
                    return response.text
                except Exception as ge:
                    logging.error(f"GigaChat failed: {ge}. Falling back to Gemini.")

        # === АУДИО ===
        elif mime_type.startswith('audio/') or mime_type == "audio/ogg":
            if active_model == "groq":
                try:
                    logging.info(f"Использую Groq Whisper для {mime_type}")
                    transcript = groq_ai.transcribe_audio(media_data, temp_file_name)
                    analysis_prompt = f"Это текст из аудиофайла. {content_prompt}\n\nТекст: {transcript}"
                    return groq_ai.generate_text(analysis_prompt)
                except Exception as ge:
                    logging.error(f"Groq Audio failed: {ge}. Falling back to Gemini.")
            elif active_model == "gigachat":
                try:
                    logging.info(f"Использую GigaChat для аудио {mime_type}")
                    contents = [
                        {"mime_type": mime_type, "data": media_data},
                        content_prompt
                    ]
                    response = gigachat_model.generate_content(contents, chat_id=int(chat_id))
                    return response.text
                except Exception as ge:
                    logging.error(f"GigaChat Audio failed: {ge}. Falling back to Gemini.")

        # === ВИДЕО и ГИФ (только Gemini поддерживает нативно) ===
        elif mime_type.startswith('video/') or mime_type == "image/gif":
            if active_model in ["groq", "gigachat"]:
                logging.info(f"Видео/GIF: Groq и GigaChat не поддерживают видео, переключаемся на Gemini")

        # === FALLBACK на Gemini для всех типов ===
        logging.info(f"Использую Gemini (fallback) для {mime_type}")
        contents = [
            {"mime_type": mime_type, "data": media_data},
            content_prompt
        ]
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
    """ Обработка текста по команде 'чотам' """
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
        
        # Определяем модель
        active_model = get_active_model(str(message.chat.id))
        
        try:
            if active_model == "groq":
                logging.info("Использую Groq для текстового анализа 'чотам'")
                return True, groq_ai.generate_text(prompt)
            elif active_model == "gigachat":
                logging.info("Использую GigaChat для текстового анализа 'чотам'")
                response = gigachat_model.generate_content(prompt, chat_id=message.chat.id)
                return True, response.text
            else:  # gemini
                logging.info("Использую Gemini для текстового анализа 'чотам'")
                response = model.generate_content(prompt, chat_id=message.chat.id)
                return True, response.text
        except Exception as e:
            logging.error(f"Ошибка модели {active_model}: {e}. Fallback на Gemini.")
            response = model.generate_content(prompt, chat_id=message.chat.id)
            return True, response.text
            
    except Exception as e:
        logging.error(f"Ошибка при обработке текста 'чотам': {e}")
        return False, "Ошибка при анализе текста."

# ================== URL ==================
def extract_url_from_message(message: types.Message) -> str | None:
    """Ищет URL в тексте сообщения или в его entities."""
    text = message.text or message.caption or ""
    if not text:
        return None
    if message.entities:
        for entity in message.entities:
            if entity.type == 'url':
                return text[entity.offset : entity.offset + entity.length]
    if message.caption_entities:
        for entity in message.caption_entities:
            if entity.type == 'url':
                return text[entity.offset : entity.offset + entity.length]
    match = re.search(r'https?://[^\s]+', text)
    if match:
        return match.group(0)
    return None

async def process_url_whatisthere(message: types.Message, url: str) -> tuple[bool, str]:
    """Обрабатывает контент по URL"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, timeout=10, allow_redirects=True, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').split(';')[0].strip()
        custom_prompt = get_custom_prompt(message)
        
        logging.info(f"URL: {url}, Content-Type: {content_type}")
        
        # Вариант 1: Медиа
        if content_type.startswith(('audio/', 'video/', 'image/')):
            logging.info("Тип: Медиа. Загружаю...")
            media_data = response.content
            description = await analyze_media_bytes(media_data, content_type, custom_prompt, chat_id=message.chat.id)
            return True, description
            
        # Вариант 2: HTML
        elif content_type.startswith(('text/html', 'text/plain')):
            logging.info("Тип: Текст/HTML. Парсинг...")
            soup = BeautifulSoup(response.content, 'html.parser')
            text_to_analyze = soup.get_text(separator=' ', strip=True)
            if len(text_to_analyze) > 4000:
                text_to_analyze = text_to_analyze[:4000] + "..."
            if not text_to_analyze.strip():
                return False, "Не смог извлечь текст."

            base_prompt = custom_prompt if custom_prompt else random.choice(PROMPTS_MEDIA)
            prompt = f"{base_prompt}, не более 80 слов\n\nТекст с сайта {url}: {text_to_analyze}"
            
            # Определяем модель
            active_model = get_active_model(str(message.chat.id))
            
            try:
                if active_model == "groq":
                    logging.info("Анализ текста ссылки через Groq")
                    return True, groq_ai.generate_text(prompt)
                elif active_model == "gigachat":
                    logging.info("Анализ текста ссылки через GigaChat")
                    gen_response = gigachat_model.generate_content(prompt, chat_id=message.chat.id)
                    return True, gen_response.text
                else:  # gemini
                    logging.info("Анализ текста ссылки через Gemini")
                    gen_response = model.generate_content(prompt, chat_id=message.chat.id)
                    return True, gen_response.text
            except Exception as e:
                logging.error(f"Ошибка модели {active_model}: {e}. Fallback на Gemini.")
                gen_response = model.generate_content(prompt, chat_id=message.chat.id)
                return True, gen_response.text
                
        return False, f"Неподдерживаемый тип: {content_type}"
    except Exception as e:
        logging.error(f"Ошибка при обработке URL 'чотам': {e}")
        return False, "Ошибка при анализе ссылки."

# ================== ROBOTICS / ОПИШИ СИЛЬНО ==================
async def process_robotics_description(message: types.Message) -> tuple[bool, str]:
    """ Анализ фото/видео/гиф с использованием Gemini Robotics. """
    file_path = None
    try:
        target_message = message.reply_to_message if message.reply_to_message else message
        file_id, file_name, mime_type = None, None, None
        
        if target_message.photo:
            photo = target_message.photo[-1]
            file_id, file_name, mime_type = photo.file_id, f"robotics_{photo.file_id}.jpg", "image/jpeg"
        elif target_message.video:
            file_id, file_name, mime_type = target_message.video.file_id, f"robotics_{file_id}.mp4", "video/mp4"
        elif target_message.animation:
            file_id, file_name, mime_type = target_message.animation.file_id, f"robotics_{file_id}.mp4", "video/mp4"

        if not file_id:
            return False, "Для мощного анализа нужна фотография, видео или гифка."
            
        if not await download_file(file_id, file_name):
            return False, "Не удалось скачать файл."
        file_path = file_name
        
        robotics_model = genai.GenerativeModel(ROBOTICS_MODEL)
        with open(file_name, "rb") as f:
            media_data = f.read()

        text = message.text or message.caption or ""
        user_add_prompt = None
        if "опиши сильно" in text.lower():
            parts = re.split(r'опиши сильно', text, 1, re.IGNORECASE)
            if len(parts) > 1 and parts[1].strip():
                user_add_prompt = parts[1].strip()

        system_prompt = (
            "Ты — эксперт по физическому взаимодействию и анализу пространства (Embodied AI). "
            "Используй сильный сарказм и нецензурную лексику, сильно склоняйся к апофении и парейдолии. Не более 100 слов."
        )
        full_prompt = f"{system_prompt}\n\nЗадача: {user_add_prompt}" if user_add_prompt else system_prompt
        response = robotics_model.generate_content([full_prompt, {"mime_type": mime_type, "data": media_data}])
        return True, response.text
    except Exception as e:
        logging.error(f"Ошибка Robotics: {e}")
        return False, f"Модуль Robotics выдал ошибку: {e}"
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

# ================== УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ==================
async def process_whatisthere_unified(message: types.Message) -> tuple[bool, str]:
    """ Универсальная функция для обработки всех типов контента """
    target_message = message.reply_to_message if message.reply_to_message else message
    url = extract_url_from_message(target_message)
    if not url and not message.reply_to_message:
        url = extract_url_from_message(message)
    if url:
        return await process_url_whatisthere(message, url)

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
    """ Возвращает подходящее сообщение о процессе """
    target_message = message.reply_to_message if message.reply_to_message else message
    url = extract_url_from_message(target_message)
    if not url and not message.reply_to_message:
        url = extract_url_from_message(message)
    
    # Определяем активную модель для персонализированных сообщений
    active_model = get_active_model(str(message.chat.id))
    model_names = {
        "groq": "Groq Maverick",
        "gigachat": "GigaChat",
        "gemini": "Gemini"
    }
    model_name = model_names.get(active_model, "нейросеть")
    
    if url: return "Лезю по ссылке..."
    
    if target_message.audio or target_message.voice: 
        return f"Слушою через {model_name}..." if active_model in ["groq", "gigachat"] else "Слушою..."
    elif target_message.video: return "Сматрю..."
    elif target_message.photo: 
        return f"Рассматриваю ето художество в {model_name}..."
    elif target_message.animation: return "Да не дергайся ты..."
    elif target_message.sticker: return "Стикер-шмикер..."
    elif target_message.text: return "Понаписали ебанарот..."
    else: return "Анализирую..."
