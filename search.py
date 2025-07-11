import os
import random
import logging
import requests
from googleapiclient.discovery import build
from aiogram import types
from aiogram.types import FSInputFile, Message
from config import GOOGLE_API_KEY, SEARCH_ENGINE_ID, giphy_api_key

# Функция поиска изображений через Google Custom Search API
def get_google_service():
    service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
    return service

def search_images(query: str):
    service = get_google_service()
    result = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, searchType='image').execute()
    items = result.get("items", [])
    image_urls = [item["link"] for item in items]
    return image_urls

# Настройка поиска животных
async def handle_message(message: types.Message, query, temp_img_path, error_msg):
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

# Вынесенная обработка для "найди"
async def process_image_search(query: str) -> tuple[bool, str, bytes | None]:
    """
    Обрабатывает поиск изображения по запросу.
    
    Args:
        query: Поисковый запрос
    
    Returns:
        tuple: (успех, сообщение, данные изображения)
    """
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
    """
    Сохраняет и отправляет найденное изображение.
    
    Args:
        message: Объект сообщения
        image_data: Бинарные данные изображения
    """
    temp_img_path = "searched_image.jpg"
    try:
        with open(temp_img_path, "wb") as f:
            f.write(image_data)
        photo = FSInputFile(temp_img_path)
        await message.reply_photo(photo=photo)
    finally:
        if os.path.exists(temp_img_path):
            os.remove(temp_img_path)
            
# Функция поиска GIF-изображений
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
        gif_urls = [gif['images']['original']['url'] for gif in gifs]
        return gif_urls
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка при обращении к Giphy API: {e}")
        return []

# Вынесенная функция для "котогиф"
async def process_gif_search(search_query: str) -> tuple[bool, str, bytes | None]:
    """
    Ищет и загружает случайную гифку по запросу.
    
    Args:
        search_query: Поисковый запрос для гифки
    
    Returns:
        tuple: (успех, сообщение об ошибке, данные гифки)
    """
    logging.info(f"Начало поиска гифки по запросу: '{search_query}'")
    
    try:
        gif_urls = search_gifs(search_query)
        
        if not gif_urls:
            logging.warning("Не найдено подходящих гифок")
            return False, "Не удалось найти гифку 😿", None
            
        random_gif_url = random.choice(gif_urls)
        logging.info(f"Выбран случайный URL: {random_gif_url}")
        
        response = requests.get(random_gif_url)
        
        if response.status_code == 200:
            logging.info("Гифка успешно загружена")
            return True, "", response.content
        else:
            error_msg = f"Не удалось скачать гифку: {random_gif_url}"
            logging.warning(error_msg)
            return False, error_msg, None
            
    except Exception as e:
        error_msg = f"Ошибка при загрузке гифки: {e}"
        logging.error(error_msg)
        return False, "Произошла ошибка при отправке гифки 😿", None

async def save_and_send_gif(message: types.Message, gif_data: bytes) -> None:
    """
    Сохраняет и отправляет гифку.
    
    Args:
        message: Объект сообщения
        gif_data: Бинарные данные гифки
    """
    temp_gif_path = "temp_cat.gif"
    try:
        logging.info("Начало сохранения гифки")
        with open(temp_gif_path, "wb") as f:
            f.write(gif_data)
            
        gif = FSInputFile(temp_gif_path)
        await message.reply_document(gif)
        logging.info("Гифка успешно отправлена")
        
    except Exception as e:
        logging.error(f"Ошибка при сохранении/отправке гифки: {e}")
        await message.reply("Произошла ошибка при отправке гифки 😿")
        
    finally:
        if os.path.exists(temp_gif_path):
            os.remove(temp_gif_path)
            logging.info("Временный файл удален")

async def process_gif_search(search_query: str) -> tuple[bool, str, bytes | None]:
    """
    Ищет и загружает случайную гифку по запросу.
    
    Args:
        search_query: Поисковый запрос для гифки
    
    Returns:
        tuple: (успех, сообщение об ошибке, данные гифки)
    """
    logging.info(f"Начало поиска гифки по запросу: '{search_query}'")
    
    try:
        gif_urls = search_gifs(search_query)
        
        if not gif_urls:
            logging.warning("Не найдено подходящих гифок")
            return False, "Не удалось найти гифку 😿", None
            
        random_gif_url = random.choice(gif_urls)
        logging.info(f"Выбран случайный URL: {random_gif_url}")
        
        response = requests.get(random_gif_url)
        
        if response.status_code == 200:
            logging.info("Гифка успешно загружена")
            return True, "", response.content
        else:
            error_msg = f"Не удалось скачать гифку: {random_gif_url}"
            logging.warning(error_msg)
            return False, error_msg, None
            
    except Exception as e:
        error_msg = f"Ошибка при загрузке гифки: {e}"
        logging.error(error_msg)
        return False, "Произошла ошибка при отправке гифки 😿", None