import os
import random
import logging
import requests
from googleapiclient.discovery import build
from aiogram import types
from aiogram.types import FSInputFile, Message
from config import GOOGLE_API_KEY, SEARCH_ENGINE_ID, giphy_api_key, search_model
import google.generativeai as genai
# ============== СУЩЕСТВУЮЩИЙ КОД (Google Image Search, Giphy) ==============
def get_google_service():
    service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
    return service
def search_images(query: str):
    service = get_google_service()
    result = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, searchType='image').execute()
    items = result.get("items", [])
    image_urls = [item["link"] for item in items]
    return image_urls
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
async def process_image_search(query: str) -> tuple[bool, str, bytes | None]:
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
    temp_img_path = "searched_image.jpg"
    try:
        with open(temp_img_path, "wb") as f:
            f.write(image_data)
        photo = FSInputFile(temp_img_path)
        await message.reply_photo(photo=photo)
    finally:
        if os.path.exists(temp_img_path):
            os.remove(temp_img_path)
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
async def process_gif_search(search_query: str) -> tuple[bool, str, bytes | None]:
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
# ============== НОВЫЙ КОД: GROUNDING WITH GOOGLE SEARCH ==============
location_awaiting = {}
async def handle_grounding_search(query: str) -> str:
    try:
        logging.info(f"Grounding Search запрос: {query}")
        response = search_model.generate_content(
            query,
            tools=[genai.Tool(google_search=genai.GoogleSearch())]
        )
        if response and response.text:
            logging.info(f"Grounding Search успешно выполнен")
            return response.text
        else:
            return "Не удалось получить информацию, попробуй переформулировать запрос."
    except Exception as e:
        logging.error(f"Ошибка при Grounding Search: {e}")
        return f"Произошла ошибка при поиске: {str(e)}"
async def handle_grounding_search_command(message: Message):
    query = message.text[len("упупа скажи"):].strip()
    if not query:
        await message.reply("Чё сказать-то, еблан?")
        return
    await message.bot.send_chat_action(message.chat.id, "typing")
    response = await handle_grounding_search(query)
    await message.reply(response)
# ============== НОВЫЙ КОД: GROUNDING WITH GOOGLE MAPS ==============
async def start_location_request(message: types.Message, user_id: int):
    location_awaiting[user_id] = {"stage": "waiting_location"}
    await message.reply("Ну давай, кидай свой адрес, посмотрим что там у тебя.")
async def handle_location_input(message: types.Message, user_id: int, location_text: str):
    if user_id in location_awaiting and location_awaiting[user_id]["stage"] == "waiting_location":
        location_awaiting[user_id] = {
            "stage": "waiting_query",
            "location": location_text,
            "message_id": message.message_id
        }
        await message.reply(f"Ну и хули ты хочешь по адресу {location_text}")
        return True
    return False
async def handle_location_query(message: types.Message, user_id: int, query: str) -> str:
    if user_id not in location_awaiting or location_awaiting[user_id]["stage"] != "waiting_query":
        return None
    location = location_awaiting[user_id]["location"]
    try:
        logging.info(f"Google Maps Grounding запрос: {query} для локации {location}")
        full_query = f"{query} рядом с {location}"
        response = search_model.generate_content(
            full_query,
            tools=[genai.Tool(google_search=genai.GoogleSearch())]
        )
        del location_awaiting[user_id]
        if response and response.text:
            logging.info(f"Google Maps Grounding успешно выполнен")
            sarcastic_prefix = random.choice([
                "Ну охуеть теперь, держи свои варианты:\n\n",
                "Слушай, я тут для тебя постарался:\n\n",
                "Вот что нашлось, хотя хуй знает, зачем тебе это:\n\n",
                "Ладно, смотри что я накопал:\n\n",
                "Держи, только не говори потом что я тебе хуйню посоветовал:\n\n"
            ])
            sarcastic_suffix = random.choice([
                "\n\nНу вот, доволен теперь?",
                "\n\nЧё, поможет?",
                "\n\nТеперь свали отсюда 😏",
                "\n\nЕщё что-нибудь захочешь - сам ищи.",
                "\n\nВот такие дела, бро."
            ])
            return sarcastic_prefix + response.text + sarcastic_suffix
        else:
            return "Хуй там что-то нашлось по твоему адресу. Может, ты в жопе мира живешь?"
    except Exception as e:
        logging.error(f"Ошибка при Google Maps Grounding: {e}")
        if user_id in location_awaiting:
            del location_awaiting[user_id]
        return f"Чёт накосячило при поиске: {str(e)}"
async def handle_location_address(message: Message, user_id: int):
    if message.location:
        location_text = f"координаты: {message.location.latitude}, {message.location.longitude}"
        await handle_location_input(message, user_id, location_text)
    elif message.text:
        await handle_location_input(message, user_id, message.text)
async def handle_location_query_command(message: Message, user_id: int):
    await message.bot.send_chat_action(message.chat.id, "typing")
    response = await handle_location_query(message, user_id, message.text)
    if response is not None:
        await message.reply(response)
        return True
    return False
def is_waiting_for_location(user_id: int) -> bool:
    return user_id in location_awaiting and location_awaiting[user_id]["stage"] == "waiting_location"
def is_waiting_for_query(user_id: int, message_id: int = None) -> bool:
    if user_id not in location_awaiting or location_awaiting[user_id]["stage"] != "waiting_query":
        return False
    if message_id is not None:
        return location_awaiting[user_id].get("message_id") == message_id
    return True
def cancel_location_request(user_id: int):
    if user_id in location_awaiting:
        del location_awaiting[user_id]
def get_location_state(user_id: int) -> dict | None:
    return location_awaiting.get(user_id)
async def handle_cancel_location(message: Message, user_id: int):
    cancel_location_request(user_id)
    await message.reply("Ладно, забыли про локацию.")
