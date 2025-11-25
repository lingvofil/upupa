import os
import random
import logging
import requests
import textwrap
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from googleapiclient.discovery import build
from aiogram import types
from aiogram.types import FSInputFile, Message
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# Импортируем все необходимые ключи и объекты из config
# Убедитесь, что в config.py есть GOOGLE_API_KEY, SEARCH_ENGINE_ID, giphy_api_key
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

# =============================================================================
# LEGACY: ФУНКЦИИ ПОИСКА (GOOGLE CUSTOM SEARCH & GIPHY)
# =============================================================================

def get_google_service():
    return build("customsearch", "v1", developerKey=GOOGLE_API_KEY)

def search_images(query: str):
    service = get_google_service()
    try:
        result = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, searchType='image').execute()
        items = result.get("items", [])
        return [item["link"] for item in items]
    except Exception as e:
        logging.error(f"Google API Error: {e}")
        return []

async def handle_message(message: types.Message, query, temp_img_path, error_msg):
    """Старая функция-хэндлер для скачивания картинки по запросу"""
    try:
        image_urls = search_images(query)
        if image_urls:
            random_image_url = random.choice(image_urls)
            img_response = requests.get(random_image_url)
            if img_response.status_code == 200:
                with open(temp_img_path, "wb") as f:
                    f.write(img_response.content)
                photo = FSInputFile(temp_img_path)
                await message.reply_photo(photo=photo)
                os.remove(temp_img_path)
            else:
                await message.reply(f"Не удалось скачать изображение: {random_image_url}")
        else:
            await message.reply(error_msg)
    except Exception as e:
        logging.error(f"Ошибка при поиске изображений: {e}")
        await message.reply("Произошла ошибка при поиске изображений.")

async def process_image_search(query: str) -> tuple[bool, str, bytes | None]:
    """Обрабатывает поиск изображения по запросу для команды 'найди'."""
    if not query:
        return False, "Шо тебе найти блядь", None
    try:
        image_urls = search_images(query)
        if not image_urls:
            return False, "Хуй", None
        
        random_image_url = random.choice(image_urls)
        img_response = requests.get(random_image_url)
        
        if img_response.status_code == 200:
            return True, "", img_response.content
        else:
            return False, f"Вот тебе сцылко: {random_image_url}", None
    except Exception as e:
        logging.error(f"Ошибка при поиске изображений через Google: {e}")
        return False, f"Да иди ты нахуй: {e}", None

async def save_and_send_searched_image(message: Message, image_data: bytes):
    """Сохраняет и отправляет найденное изображение."""
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
