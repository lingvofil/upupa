import asyncio
import hashlib
import logging
import random
import re
import textwrap
from collections import deque
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
from aiogram import types
from aiogram.types import BufferedInputFile, Message
from googleapiclient.discovery import build
from PIL import Image, ImageDraw, ImageFont

from core.loader import bot
from core.settings import API_TOKEN, GOOGLE_API_KEY, SEARCH_ENGINE_ID, giphy_api_key
from infrastructure.ai.clients import model
from prompts import PROMPT_DESCRIBE, SPECIAL_PROMPT, actions

# Кэш последних показанных картинок (храним хеши URL)
recent_images_cache = deque(maxlen=50)

# Домены, которые блокируют скачивание
BLOCKED_DOMAINS = [
    "shutterstock.com",
    "gettyimages.com",
    "istockphoto.com",
    "alamy.com",
    "depositphotos.com",
    "dreamstime.com",
    "123rf.com",
]

IMAGE_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
    "DNT": "1",
}
HTTP_TIMEOUT_SECONDS = 10.0


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


def _search_images_sync(query: str, start_index: int) -> list[str]:
    """Синхронная часть Google SDK; вызывается только через ``asyncio.to_thread``."""
    service = get_google_service()
    result = (
        service.cse()
        .list(
            q=query,
            cx=SEARCH_ENGINE_ID,
            searchType="image",
            start=start_index,
            num=10,
        )
        .execute()
    )
    return [item["link"] for item in result.get("items", [])]


async def search_images(query: str, randomize: bool = True) -> list[str]:
    """Ищет картинки, не блокируя event loop синхронным Google SDK."""
    try:
        start_index = random.randint(0, 40) if randomize else 1
        image_urls = await asyncio.to_thread(_search_images_sync, query, start_index)

        # Фильтруем заблокированные домены
        allowed_urls = [url for url in image_urls if is_url_allowed(url)]

        # Фильтруем уже показанные картинки
        fresh_urls = [
            url
            for url in allowed_urls
            if _get_url_hash(url) not in recent_images_cache
        ]

        if not fresh_urls:
            logging.info(
                "Все картинки из выдачи уже показывались или заблокированы, очищаем кэш"
            )
            recent_images_cache.clear()
            fresh_urls = allowed_urls if allowed_urls else image_urls

        return fresh_urls
    except Exception as e:
        logging.error(f"Google API Error: {e}")
        return []


async def download_image_with_headers(
    url: str,
    timeout: float = HTTP_TIMEOUT_SECONDS,
    *,
    client: httpx.AsyncClient | None = None,
) -> bytes | None:
    """Скачивает изображение асинхронно и проверяет Content-Type."""
    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
    )

    try:
        response = await http_client.get(url, headers=IMAGE_DOWNLOAD_HEADERS)
        content_type = response.headers.get("Content-Type", "")
        if response.status_code == 200 and "image" in content_type.lower():
            return response.content

        logging.warning(
            "Не удалось скачать изображение: %s, status: %s, content-type: %s",
            url,
            response.status_code,
            content_type,
        )
        return None
    except httpx.TimeoutException:
        logging.error(f"Timeout при скачивании: {url}")
        return None
    except httpx.HTTPError as e:
        logging.error(f"Ошибка при скачивании {url}: {e}")
        return None
    finally:
        if owns_client:
            await http_client.aclose()


async def handle_message(message: types.Message, query, temp_img_path, error_msg):
    try:
        image_urls = await search_images(query, randomize=True)
        if not image_urls:
            await message.reply(error_msg)
            return

        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            # Пытаемся скачать несколько картинок
            for image_url in image_urls[:5]:
                image_data = await download_image_with_headers(
                    image_url,
                    client=client,
                )

                if image_data:
                    recent_images_cache.append(_get_url_hash(image_url))
                    filename = Path(str(temp_img_path)).name or "searched_image.jpg"
                    photo = BufferedInputFile(image_data, filename=filename)
                    await message.reply_photo(photo=photo)
                    return

        await message.reply("Все ссылки оказались битыми 😢")
    except Exception as e:
        logging.error(f"Ошибка handle_message: {e}")
        await message.reply("Ошибка при поиске.")


async def process_image_search(
    query: str,
    max_attempts: int = 5,
) -> tuple[bool, str, bytes | None]:
    """Ищет и скачивает изображение с несколькими попытками."""
    if not query:
        return False, "Шо тебе найти блядь", None

    try:
        image_urls = await search_images(query, randomize=True)
        if not image_urls:
            return False, "Хуй", None

        random.shuffle(image_urls)

        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            for attempts, image_url in enumerate(image_urls, start=1):
                if attempts > max_attempts:
                    break

                logging.info(f"Попытка {attempts}/{max_attempts}: {image_url}")
                image_data = await download_image_with_headers(
                    image_url,
                    client=client,
                )

                if image_data:
                    recent_images_cache.append(_get_url_hash(image_url))
                    return True, "", image_data

                logging.warning(f"Пропускаем битую ссылку: {image_url}")

        return False, "Все ссылки оказались битыми, попробуй другой запрос", None
    except Exception as e:
        logging.error(f"Ошибка process_image_search: {e}")
        return False, f"Ошибка: {e}", None


async def save_and_send_searched_image(message: Message, image_data: bytes):
    photo = BufferedInputFile(image_data, filename="searched_image.jpg")
    await message.reply_photo(photo=photo)


# --- GIPHY FUNCTIONS ---


async def search_gifs(
    query: str = "cat",
    *,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    url = "https://api.giphy.com/v1/gifs/search"
    lang = "ru" if re.search(r"[а-яё]", query, re.IGNORECASE) else "en"
    params = {
        "api_key": giphy_api_key,
        "q": query,
        "limit": 10,
        "offset": 0,
        "rating": "g",
        "lang": lang,
    }

    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
    )

    try:
        response = await http_client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        gifs = data.get("data", [])
        return [gif["images"]["original"]["url"] for gif in gifs]
    except httpx.HTTPError as e:
        logging.error(f"Ошибка при обращении к Giphy API: {e}")
        return []
    finally:
        if owns_client:
            await http_client.aclose()


async def process_gif_search(
    search_query: str,
) -> tuple[bool, str, bytes | None]:
    logging.info(f"Начало поиска гифки по запросу: '{search_query}'")
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            gif_urls = await search_gifs(search_query, client=client)
            if not gif_urls:
                return False, "Не удалось найти гифку 😿", None

            random_gif_url = random.choice(gif_urls)
            response = await client.get(random_gif_url)

        if response.status_code == 200:
            return True, "", response.content

        error_msg = f"Не удалось скачать гифку: {random_gif_url}"
        return False, error_msg, None
    except Exception as e:
        error_msg = f"Ошибка при загрузке гифки: {e}"
        logging.error(error_msg)
        return False, "Произошла ошибка при отправке гифки 😿", None


async def save_and_send_gif(message: types.Message, gif_data: bytes) -> None:
    try:
        gif = BufferedInputFile(gif_data, filename="result.gif")
        await message.reply_document(gif)
    except Exception as e:
        logging.error(f"Ошибка при сохранении/отправке гифки: {e}")
        await message.reply("Произошла ошибка при отправке гифки 😿")


# =============================================================================
# ФУНКЦИИ ОБРАБОТКИ ИЗОБРАЖЕНИЙ (AI EDIT & DESCRIBE)
# =============================================================================


async def handle_add_text_command(message: types.Message):
    """Полностью обрабатывает команду 'добавь'."""
    try:
        await bot.send_chat_action(
            chat_id=message.chat.id,
            action=random.choice(actions),
        )
        photo = await get_photo_from_message(message)
        if not photo:
            await message.reply("Изображение для обработки не найдено.")
            return

        image_bytes = await download_telegram_image(bot, photo)
        generated_text = await process_image(image_bytes)
        modified_image = await asyncio.to_thread(
            overlay_text_on_image,
            image_bytes,
            generated_text,
        )

        photo_file = BufferedInputFile(
            modified_image,
            filename="modified_image.jpg",
        )
        await message.reply_photo(photo_file)
    except Exception as e:
        logging.error(f"Ошибка в handle_add_text_command: {e}", exc_info=True)
        await message.reply("Произошла непредвиденная ошибка при обработке изображения.")


async def process_image_description(bot, message: types.Message) -> tuple[bool, str]:
    """Основная функция для обработки команды 'опиши'."""
    try:
        await bot.send_chat_action(
            chat_id=message.chat.id,
            action=random.choice(actions),
        )
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


async def _download_telegram_file(bot, file_id: str) -> bytes | None:
    file = await bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{API_TOKEN}/{file.file_path}"

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        response = await client.get(file_url)

    return response.content if response.status_code == 200 else None


async def download_image(bot, file_id: str) -> bytes | None:
    try:
        return await _download_telegram_file(bot, file_id)
    except Exception as e:
        logging.error(f"Ошибка в download_image: {e}", exc_info=True)
        return None


async def generate_image_description(image_data: bytes) -> tuple[bool, str]:
    try:
        response = await asyncio.to_thread(
            model.generate_content,
            [
                PROMPT_DESCRIBE,
                {"mime_type": "image/jpeg", "data": image_data},
            ],
        )
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
                if doc.mime_type and doc.mime_type.startswith("image/"):
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
    image_data = await _download_telegram_file(bot, photo.file_id)
    if image_data is None:
        raise RuntimeError("Не удалось загрузить изображение.")
    return image_data


async def process_image(image_bytes: bytes) -> str:
    try:
        response = await asyncio.to_thread(
            model.generate_content,
            [
                SPECIAL_PROMPT,
                {"mime_type": "image/jpeg", "data": image_bytes},
            ],
        )
        return response.text
    except Exception as e:
        logging.error(f"Ошибка обработки изображения: {e}", exc_info=True)
        raise RuntimeError(f"Ошибка генерации текста: {e}") from e


def get_text_size(font, text):
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def overlay_text_on_image(image_bytes: bytes, text: str) -> bytes:
    """Накладывает текст и возвращает JPEG в памяти, без общего temp-файла."""
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

    rectangle = Image.new(
        "RGBA",
        (image.width, text_block_height + 40),
        (0, 0, 0, 128),
    )
    image.paste(rectangle, (0, y - 5), rectangle)

    for line in lines:
        text_width, _ = get_text_size(font, line)
        x = (image.width - text_width) / 2
        draw.text((x, y), line, font=font, fill="white")
        y += line_height + 10

    output = BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()
