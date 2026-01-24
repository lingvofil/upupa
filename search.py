import os
import random
import logging
import requests
import textwrap
from io import BytesIO
from urllib.parse import urlparse
from PIL import Image, ImageDraw, ImageFont
from googleapiclient.discovery import build
from aiogram import types
from aiogram.types import FSInputFile, Message
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from google.generativeai import protos
from collections import deque
import hashlib

# Импортируем ключи и объекты из config
from config import (
    API_TOKEN, 
    model, 
    bot, 
    search_model, 
    GOOGLE_API_KEY, 
    SEARCH_ENGINE_ID, 
    giphy_api_key
)
from prompts import PROMPT_DESCRIBE, SPECIAL_PROMPT, actions

# Кэш последних показанных картинок (храним хеши URL)
recent_images_cache = deque(maxlen=50)

# Домены, которые блокируют скачивание
BLOCKED_DOMAINS = [
    'shutterstock.com',
    'gettyimages.com',
    'istockphoto.com',
    'alamy.com',
    'depositphotos.com',
    'dreamstime.com',
    '123rf.com',
]

# =============================================================================
# LEGACY: ПОИСК КАРТИНОК (GOOGLE CUSTOM SEARCH)
# =============================================================================

def get_google_service():
    return build("customsearch", "v1", developerKey=GOOGLE_API_KEY)

def _get_url_hash(url: str) -> str:
    """Создает короткий хеш URL для кэша."""
    return hashlib.md5(url.encode()).hexdigest()[:16]

def is_url_allowed(url: str) -> bool:
    """Проверяет, не из заблокированного ли домена URL."""
    domain = urlparse(url).netloc.lower()
    return not any(blocked in domain for blocked in BLOCKED_DOMAINS)

def download_image_with_headers(url: str, timeout: int = 10) -> bytes | None:
    """Скачивает изображение с proper headers для обхода блокировок."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.google.com/',
        'DNT': '1',
        'Connection': 'keep-alive',
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        
        # Проверяем, что это действительно изображение
        content_type = response.headers.get('Content-Type', '')
        if response.status_code == 200 and 'image' in content_type:
            return response.content
        else:
            logging.warning(f"Не удалось скачать изображение: {url}, status: {response.status_code}, content-type: {content_type}")
            return None
            
    except requests.exceptions.Timeout:
        logging.error(f"Timeout при скачивании: {url}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка при скачивании {url}: {e}")
        return None

def search_images(query: str, randomize: bool = True):
    """
    Поиск картинок с рандомизацией выдачи и фильтрацией проблемных доменов.
    
    Args:
        query: поисковый запрос
        randomize: если True, добавляет случайное смещение к результатам
    """
    try:
        service = get_google_service()
        
        # Рандомизация: случайное смещение от 0 до 40
        start_index = random.randint(0, 40) if randomize else 1
        
        result = service.cse().list(
            q=query, 
            cx=SEARCH_ENGINE_ID, 
            searchType='image',
            start=start_index,
            num=10
        ).execute()
        
        items = result.get("items", [])
        image_urls = [item["link"] for item in items]
        
        # Фильтруем заблокированные домены
        allowed_urls = [url for url in image_urls if is_url_allowed(url)]
        
        # Фильтруем уже показанные картинки
        fresh_urls = [url for url in allowed_urls if _get_url_hash(url) not in recent_images_cache]
        
        if not fresh_urls:
            logging.info("Все картинки из выдачи уже показывались или заблокированы, очищаем кэш")
            recent_images_cache.clear()
            fresh_urls = allowed_urls if allowed_urls else image_urls
        
        return fresh_urls
        
    except Exception as e:
        logging.error(f"Google API Error: {e}")
        return []

async def handle_message(message: types.Message, query, temp_img_path, error_msg):
    try:
        image_urls = search_images(query, randomize=True)
        if not image_urls:
            await message.reply(error_msg)
            return
        
        # Пытаемся скачать несколько картинок
        for image_url in image_urls[:5]:  # Пробуем первые 5
            image_data = download_image_with_headers(image_url)
            
            if image_data:
                recent_images_cache.append(_get_url_hash(image_url))
                
                with open(temp_img_path, "wb") as f:
                    f.write(image_data)
                photo = FSInputFile(temp_img_path)
                await message.reply_photo(photo=photo)
                os.remove(temp_img_path)
                return
        
        # Если ни одна не скачалась
        await message.reply("Все ссылки оказались битыми 😢")
        
    except Exception as e:
        logging.error(f"Ошибка handle_message: {e}")
        await message.reply("Ошибка при поиске.")

async def process_image_search(query: str, max_attempts: int = 5) -> tuple[bool, str, bytes | None]:
    """
    Ищет и скачивает изображение с несколькими попытками.
    
    Args:
        query: поисковый запрос
        max_attempts: максимальное количество попыток скачать разные картинки
    """
    if not query:
        return False, "Шо тебе найти блядь", None
    
    try:
        image_urls = search_images(query, randomize=True)
        if not image_urls:
            return False, "Хуй", None
        
        # Пытаемся скачать несколько картинок, пока не найдем рабочую
        attempts = 0
        random.shuffle(image_urls)
        
        for image_url in image_urls:
            if attempts >= max_attempts:
                break
                
            attempts += 1
            logging.info(f"Попытка {attempts}/{max_attempts}: {image_url}")
            
            image_data = download_image_with_headers(image_url)
            
            if image_data:
                recent_images_cache.append(_get_url_hash(image_url))
                return True, "", image_data
            else:
                logging.warning(f"Пропускаем битую ссылку: {image_url}")
                continue
        
        # Если все попытки неудачны
        return False, "Все ссылки оказались битыми, попробуй другой запрос", None
        
    except Exception as e:
        logging.error(f"Ошибка process_image_search: {e}")
        return False, f"Ошибка: {e}", None

async def save_and_send_searched_image(message: Message, image_data: bytes):
    temp_img_path = "searched_image.jpg"
    try:
        with open(temp_img_path, "wb") as f:
            f.write(image_data)
        photo = FSInputFile(temp_img_path)
        await message.reply_photo(photo=photo)
    finally:
        if os.path.exists(temp_img_path):
            os.remove(temp_img_path)

# --- GIPHY FUNCTIONS ---

def search_gifs(query: str = "cat"):
    url = 'https://api.giphy.com/v1/gifs/search'
    params = {
        'api_key': giphy_api_key,
        'q': query,
        'limit': 10,
        'offset': 0,
        'rating': 'g',
        'lang': 'en'
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        gifs = data.get('data', [])
        return [gif['images']['original']['url'] for gif in gifs]
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка при обращении к Giphy API: {e}")
        return []

async def process_gif_search(search_query: str) -> tuple[bool, str, bytes | None]:
    logging.info(f"Начало поиска гифки по запросу: '{search_query}'")
    try:
        gif_urls = search_gifs(search_query)
        if not gif_urls:
            return False, "Не удалось найти гифку 😿", None
        
        random_gif_url = random.choice(gif_urls)
        response = requests.get(random_gif_url)
        
        if response.status_code == 200:
            return True, "", response.content
        else:
            error_msg = f"Не удалось скачать гифку: {random_gif_url}"
            return False, error_msg, None
    except Exception as e:
        error_msg = f"Ошибка при загрузке гифки: {e}"
        logging.error(error_msg)
        return False, "Произошла ошибка при отправке гифки 😿", None

async def save_and_send_gif(message: types.Message, gif_data: bytes) -> None:
    temp_gif_path = "temp_cat.gif"
    try:
        with open(temp_gif_path, "wb") as f:
            f.write(gif_data)
        gif = FSInputFile(temp_gif_path)
        await message.reply_document(gif)
    except Exception as e:
        logging.error(f"Ошибка при сохранении/отправке гифки: {e}")
        await message.reply("Произошла ошибка при отправке гифки 😿")
    finally:
        if os.path.exists(temp_gif_path):
            os.remove(temp_gif_path)

# =============================================================================
# НОВЫЙ ФУНКЦИОНАЛ: GEMINI GROUNDING (SEARCH & MAPS)
# =============================================================================

async def process_grounding_search(query: str) -> str:
    """Выполняет поиск информации через Gemini с использованием Grounding with Google Search."""
    if not query:
        return "Ты забыл написать, что искать, гений."
    try:
        prompt = f"Найди актуальную информацию по запросу: {query}. Ответь развернуто и по делу."
        
        tool_config = [protos.Tool(google_search=protos.GoogleSearch())]
        
        response = await search_model.generate_content_async(
            prompt,
            tools=tool_config, 
            safety_settings={
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
        )
        if response.text:
            return response.text
        elif response.parts:
            return "".join([part.text for part in response.parts])
        else:
            return "Гугл молчит, как партизан. Ничего не нашел."
    except Exception as e:
        logging.error(f"Ошибка Grounding search: {e}")
        return f"Что-то пошло не так при поиске. Ошибка: {e}"

async def process_location_search(address: str, user_request: str) -> str:
    """Ищет места рядом с адресом и отвечает в иронично-грубом стиле."""
    try:
        prompt = (
            f"Пользователь находится по адресу: {address}. "
            f"Его запрос: {user_request}. "
            f"Найди информацию об этом через Google Search (используй карты/места). "
            f"Ответь пользователю в ироничном стиле, с сарказмом и немного используй нецензурную лексику (мат). "
            f"Не будь душным, будь дерзким, но дай полезную информацию (названия, рейтинг, открыто ли)."
        )
        
        tool_config = [protos.Tool(google_search=protos.GoogleSearch())]
        
        response = await search_model.generate_content_async(
            prompt,
            tools=tool_config,
            safety_settings={
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
            }
        )
        
        if response.text:
            return response.text
        elif response.parts:
            return "".join([part.text for part in response.parts])
        else:
            return "Бля, ничего не нашел в этой дыре."
    except Exception as e:
        logging.error(f"Ошибка Location search: {e}")
        return "Я сломался, пока искал эту херню."

# =============================================================================
# ФУНКЦИИ ОБРАБОТКИ ИЗОБРАЖЕНИЙ (AI EDIT & DESCRIBE)
# =============================================================================

async def handle_add_text_command(message: types.Message):
    """Полностью обрабатывает команду 'добавь'."""
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
        photo = await get_photo_from_message(message)
        if not photo:
            await message.reply("Изображение для обработки не найдено.")
            return

        image_bytes = await download_telegram_image(bot, photo)
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
                logging.error(f"Не удалось удалить временный файл: {e}")

async def process_image_description(bot, message: types.Message) -> tuple[bool, str]:
    """Основная функция для обработки команды 'опиши'."""
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
        photo = await get_photo_from_message(message)
        if not photo:
            return False, "Изображение для описания не найдено."
        
        image_data = await download_image(bot, photo.file_id)
        if not image_data:
            return False, "Не удалось загрузить изображение."
        
        success, description = await generate_image_description(image_data)
        return success, description
    except Exception as e:
        logging.error(f"Ошибка в process_image_description: {e}", exc_info=True)
        return False, "Произошла ошибка при обработке изображения."

async def download_image(bot, file_id: str) -> bytes | None:
    try:
        file = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{API_TOKEN}/{file.file_path}"
        response = requests.get(file_url)
        return response.content if response.status_code == 200 else None
    except Exception as e:
        logging.error(f"Ошибка в download_image: {e}", exc_info=True)
        return None

async def generate_image_description(image_data: bytes) -> tuple[bool, str]:
    try:
        response = model.generate_content([
            PROMPT_DESCRIBE,
            {"mime_type": "image/jpeg", "data": image_data}
        ])
        return True, response.text
    except Exception as e:
        logging.error(f"Ошибка генерации описания: {e}", exc_info=True)
        return False, f"Ошибка генерации описания: {str(e)}"

async def extract_image_info(message: types.Message) -> str | None:
    try:
        if message.photo:
            return message.photo[-1].file_id
        elif message.reply_to_message:
            if message.reply_to_message.photo:
                return message.reply_to_message.photo[-1].file_id
            elif message.reply_to_message.document:
                doc = message.reply_to_message.document
                if doc.mime_type and doc.mime_type.startswith('image/'):
                    return doc.file_id
        return None
    except Exception as e:
        logging.error(f"Ошибка в extract_image_info: {e}", exc_info=True)
        return None

async def get_photo_from_message(message: types.Message):
    if message.photo:
        return message.photo[-1]
    elif message.reply_to_message:
        if message.reply_to_message.photo:
            return message.reply_to_message.photo[-1]
        return message.reply_to_message.document
    return None

async def download_telegram_image(bot, photo):
    file = await bot.get_file(photo.file_id)
    file_url = f"https://api.telegram.org/file/bot{API_TOKEN}/{file.file_path}"
    response = requests.get(file_url)
    if response.status_code != 200:
        raise Exception("Не удалось загрузить изображение.")
    return response.content

async def process_image(image_bytes: bytes) -> str:
    try:
        response = model.generate_content([
            SPECIAL_PROMPT,
            {"mime_type": "image/jpeg", "data": image_bytes}
        ])
        return response.text
    except Exception as e:
        logging.error(f"Ошибка обработки изображения: {e}", exc_info=True)
        raise RuntimeError(f"Ошибка генерации текста: {e}") from e

def get_text_size(font, text):
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]

def overlay_text_on_image(image_bytes: bytes, text: str) -> str:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    
    try:
        font = ImageFont.truetype(font_path, 48)
    except IOError:
        font = ImageFont.load_default()

    max_width = image.width - 20
    avg_char_width = 25 
    max_chars_per_line = max(1, int(max_width // avg_char_width))
    lines = textwrap.wrap(text, width=max_chars_per_line)
    
    line_height = 50
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
