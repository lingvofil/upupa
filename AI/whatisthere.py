import asyncio
import ipaddress
import logging
import os
import random
import re
import socket

import aiofiles
import httpx
from aiogram import types
from bs4 import BeautifulSoup

from core.loader import bot
from core.settings import ROBOTICS_MODEL
from core.state import chat_settings
from infrastructure.ai.clients import gemini_client, gigachat_model, groq_ai, model
from infrastructure.ai.gemini import GeminiModel
from infrastructure.ai.execution import ai_feature
from infrastructure.media_io import download_telegram_bytes
from prompts import PROMPTS_MEDIA


HTTP_TIMEOUT_SECONDS = 10.0
MAX_URL_BYTES = 50 * 1024 * 1024
MAX_URL_REDIRECTS = 5
URL_HEADERS = {"User-Agent": "Mozilla/5.0"}
ALLOWED_URL_SCHEMES = {"http", "https"}


def get_active_model(chat_id: str) -> str:
    """Возвращает активную модель для чата."""
    settings = chat_settings.get(str(chat_id), {})
    active_model = settings.get("active_model", "gemini")
    if active_model == "history":
        active_model = "gemini"
    return active_model


def get_custom_prompt(message: types.Message) -> str | None:
    """Извлекает кастомный промпт из сообщения после 'чотам'."""
    text = message.text or message.caption or ""
    if "чотам" in text.lower():
        parts = re.split(r"чотам", text, 1, re.IGNORECASE)
        if len(parts) > 1 and parts[1].strip():
            return parts[1].strip()
    return None


class UnsafeURLError(ValueError):
    """URL ведёт во внутреннюю сеть или использует запрещённую схему."""


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
        or not address.is_global
    )


async def _resolve_host_addresses(host: str, port: int) -> list[str]:
    """Возвращает все адреса, в которые резолвится хост."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def _validate_public_url(url: httpx.URL) -> None:
    """Пропускает только http(s)-URL, все адреса которых публичные."""
    if url.scheme not in ALLOWED_URL_SCHEMES:
        raise UnsafeURLError(f"Схема {url.scheme!r} не поддерживается")
    host = url.host
    if not host:
        raise UnsafeURLError("В ссылке нет хоста")

    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        port = url.port or (443 if url.scheme == "https" else 80)
        try:
            resolved = await _resolve_host_addresses(host, port)
        except OSError as exc:
            raise UnsafeURLError(f"Не удалось резолвить хост {host!r}") from exc
        # Отбрасываем scope id у IPv6 (fe80::1%eth0).
        addresses = [ipaddress.ip_address(value.split("%", 1)[0]) for value in resolved]

    if not addresses:
        raise UnsafeURLError(f"Хост {host!r} не резолвится")
    for address in addresses:
        if not _is_public_address(address):
            raise UnsafeURLError(f"Хост {host!r} указывает на непубличный адрес {address}")


async def _download_url_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_bytes: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[bytes, str]:
    """Скачивает пользовательский URL, не пуская запрос во внутреннюю сеть.

    Редиректы проходятся вручную, и каждый следующий адрес проверяется заново.
    """
    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    current_url = httpx.URL(url)
    try:
        for _ in range(MAX_URL_REDIRECTS + 1):
            await _validate_public_url(current_url)
            async with http_client.stream(
                "GET",
                current_url,
                headers=headers,
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("Location", "")
                    current_url = response.url.join(location)
                    continue

                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
                content_length = response.headers.get("Content-Length")
                if max_bytes is not None and content_length and int(content_length) > max_bytes:
                    raise ValueError(
                        f"Ответ превышает лимит {max_bytes} байт: Content-Length={content_length}"
                    )

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        raise ValueError(f"Ответ превышает лимит {max_bytes} байт")
                    chunks.append(chunk)
                return b"".join(chunks), content_type
        raise UnsafeURLError(f"Слишком много редиректов (больше {MAX_URL_REDIRECTS})")
    finally:
        if owns_client:
            await http_client.aclose()


async def _download_telegram_bytes(file_id: str) -> bytes:
    return await download_telegram_bytes(bot, file_id)


# Compatibility-функция: используется не только "чотам", но и distortion.
async def download_file(file_id: str, file_name: str) -> bool:
    """Загружает Telegram-файл на диск без блокировки event loop."""
    try:
        data = await _download_telegram_bytes(file_id)
        async with aiofiles.open(file_name, "wb") as output:
            await output.write(data)
        return True
    except Exception as e:
        logging.error(f"Ошибка при загрузке файла {file_id}: {e}")
        return False


async def _read_media_source(media_source: str | bytes) -> tuple[bytes, str]:
    if isinstance(media_source, str):
        async with aiofiles.open(media_source, "rb") as media_file:
            return await media_file.read(), os.path.basename(media_source)
    if isinstance(media_source, bytes):
        return media_source, "media_input.tmp"
    raise ValueError("Неверный тип media_source: нужен str (путь) или bytes")


async def _generate_text_with_active_model(
    prompt: str,
    active_model: str,
    chat_id: int | str,
) -> str:
    """Вызывает синхронные provider wrappers в worker thread с Gemini fallback."""
    try:
        if active_model == "groq":
            logging.info("Использую Groq для текстового анализа 'чотам'")
            return await asyncio.to_thread(groq_ai.generate_text, prompt)
        if active_model == "gigachat":
            logging.info("Использую GigaChat для текстового анализа 'чотам'")
            response = await asyncio.to_thread(
                gigachat_model.generate_content,
                prompt,
                chat_id=chat_id,
            )
            return response.text

        logging.info("Использую Gemini для текстового анализа 'чотам'")
        response = await asyncio.to_thread(
            model.generate_content,
            prompt,
            chat_id=chat_id,
        )
        return response.text
    except Exception as e:
        logging.error(f"Ошибка модели {active_model}: {e}. Fallback на Gemini.")
        response = await asyncio.to_thread(
            model.generate_content,
            prompt,
            chat_id=chat_id,
        )
        return response.text


async def analyze_media_bytes(
    media_source: str | bytes,
    mime_type: str,
    custom_prompt: str | None = None,
    chat_id: int | str | None = None,
    *,
    source_name: str | None = None,
) -> str:
    """Анализирует медиа, не блокируя event loop синхронными provider SDK."""
    try:
        media_data, temp_file_name = await _read_media_source(media_source)
        if source_name:
            temp_file_name = source_name

        if custom_prompt:
            content_prompt = f"{custom_prompt}, не более 80 слов"
        else:
            base_prompt = random.choice(PROMPTS_MEDIA)
            content_prompt = f"{base_prompt}, не более 80 слов"

        active_model = get_active_model(str(chat_id)) if chat_id else "gemini"
        logging.info(f"analyze_media_bytes: используется модель {active_model} для {mime_type}")

        if mime_type.startswith("image/") or mime_type == "image/webp":
            if active_model == "groq":
                try:
                    logging.info(f"Использую Groq с {groq_ai.vision_model} для {mime_type}")
                    return await asyncio.to_thread(
                        groq_ai.analyze_image,
                        media_data,
                        content_prompt,
                    )
                except Exception as ge:
                    logging.error(f"Groq Vision failed: {ge}. Falling back to Gemini.")
            elif active_model == "gigachat":
                try:
                    logging.info(f"Использую GigaChat для {mime_type}")
                    contents = [
                        {"mime_type": mime_type, "data": media_data},
                        content_prompt,
                    ]
                    response = await asyncio.to_thread(
                        gigachat_model.generate_content,
                        contents,
                        chat_id=int(chat_id),
                    )
                    return response.text
                except Exception as ge:
                    logging.error(f"GigaChat failed: {ge}. Falling back to Gemini.")

        elif mime_type.startswith("audio/") or mime_type == "audio/ogg":
            if active_model == "groq":
                try:
                    logging.info(f"Использую Groq Whisper для {mime_type}")
                    transcript = await asyncio.to_thread(
                        groq_ai.transcribe_audio,
                        media_data,
                        temp_file_name,
                    )
                    analysis_prompt = (
                        f"Это текст из аудиофайла. {content_prompt}\n\n"
                        f"Текст: {transcript}"
                    )
                    return await asyncio.to_thread(groq_ai.generate_text, analysis_prompt)
                except Exception as ge:
                    logging.error(f"Groq Audio failed: {ge}. Falling back to Gemini.")
            elif active_model == "gigachat":
                try:
                    logging.info(f"Использую GigaChat для аудио {mime_type}")
                    contents = [
                        {"mime_type": mime_type, "data": media_data},
                        content_prompt,
                    ]
                    response = await asyncio.to_thread(
                        gigachat_model.generate_content,
                        contents,
                        chat_id=int(chat_id),
                    )
                    return response.text
                except Exception as ge:
                    logging.error(f"GigaChat Audio failed: {ge}. Falling back to Gemini.")

        elif mime_type.startswith("video/") or mime_type == "image/gif":
            if active_model in ["groq", "gigachat"]:
                logging.info(
                    "Видео/GIF: Groq и GigaChat не поддерживают видео, переключаемся на Gemini"
                )

        logging.info(f"Использую Gemini (fallback) для {mime_type}")
        contents = [
            {"mime_type": mime_type, "data": media_data},
            content_prompt,
        ]
        response = await asyncio.to_thread(
            model.generate_content,
            contents,
            chat_id=chat_id,
        )
        return response.text
    except Exception as e:
        logging.error(f"Ошибка при анализе медиа байтов ({mime_type}): {e}")
        return "Нихуя не понял, давай еще раз."


# ================== AUDIO ==================
async def extract_audio_info(
    message: types.Message,
) -> tuple[str | None, str | None, str | None]:
    target_message = message.reply_to_message if message.reply_to_message else message
    if target_message.audio:
        return (
            target_message.audio.file_id,
            "audio/mpeg",
            f"audio_{target_message.audio.file_id}.mp3",
        )
    if target_message.voice:
        return (
            target_message.voice.file_id,
            "audio/ogg",
            f"voice_{target_message.voice.file_id}.ogg",
        )
    return None, None, None


async def _process_media_description(
    message: types.Message,
    file_id: str,
    file_name: str,
    mime_type: str,
    download_error: str,
) -> tuple[bool, str]:
    try:
        media_data = await _download_telegram_bytes(file_id)
    except Exception as e:
        logging.error(f"Ошибка при загрузке файла {file_id}: {e}")
        return False, download_error

    custom_prompt = get_custom_prompt(message)
    description = await analyze_media_bytes(
        media_data,
        mime_type,
        custom_prompt,
        chat_id=message.chat.id,
        source_name=file_name,
    )
    return True, description


async def process_audio_description(message: types.Message) -> tuple[bool, str]:
    try:
        file_id, mime_type, file_name = await extract_audio_info(message)
        if not all([file_id, mime_type, file_name]):
            return False, "Ошибка: нет аудиофайла для анализа."
        return await _process_media_description(
            message,
            file_id,
            file_name,
            mime_type,
            "Не удалось загрузить аудио.",
        )
    except Exception as e:
        logging.error(f"Ошибка при обработке аудио: {e}")
        return False, "Ошибка при анализе аудио."


# ================== VIDEO ==================
async def extract_video_info(
    message: types.Message,
) -> tuple[str | None, str, str | None]:
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
        return await _process_media_description(
            message,
            file_id,
            file_name,
            mime_type,
            "Не удалось загрузить видео.",
        )
    except Exception as e:
        logging.error(f"Ошибка при обработке видео: {e}")
        return False, "Ошибка при анализе видео."


# ================== IMAGE ==================
def extract_image_info(message: types.Message) -> tuple[str | None, str, str | None]:
    target_message = message.reply_to_message if message.reply_to_message else message
    if target_message.photo:
        photo = target_message.photo[-1]
        file_id = photo.file_id
        return file_id, f"photo_{file_id}.jpg", "image/jpeg"
    return None, "", None


async def process_image_whatisthere(message: types.Message) -> tuple[bool, str]:
    try:
        file_id, file_name, mime_type = extract_image_info(message)
        if not file_id:
            return False, "Ошибка: нет картинки для анализа."
        return await _process_media_description(
            message,
            file_id,
            file_name,
            mime_type,
            "Не удалось загрузить картинку.",
        )
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
        return await _process_media_description(
            message,
            file_id,
            file_name,
            mime_type,
            "Не удалось загрузить гифку.",
        )
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
        return await _process_media_description(
            message,
            file_id,
            file_name,
            mime_type,
            "Не удалось загрузить стикер.",
        )
    except Exception as e:
        logging.error(f"Ошибка при обработке стикера 'чотам': {e}")
        return False, "Ошибка при анализе стикера."


# ================== TEXT ==================
async def process_text_whatisthere(message: types.Message) -> tuple[bool, str]:
    """Обработка текста по команде 'чотам'."""
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
        active_model = get_active_model(str(message.chat.id))
        response = await _generate_text_with_active_model(
            prompt,
            active_model,
            message.chat.id,
        )
        return True, response
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
            if entity.type == "url":
                return text[entity.offset : entity.offset + entity.length]
    if message.caption_entities:
        for entity in message.caption_entities:
            if entity.type == "url":
                return text[entity.offset : entity.offset + entity.length]
    match = re.search(r"https?://[^\s]+", text)
    if match:
        return match.group(0)
    return None


def _extract_html_text(content: bytes) -> str:
    soup = BeautifulSoup(content, "html.parser")
    return soup.get_text(separator=" ", strip=True)


async def process_url_whatisthere(message: types.Message, url: str) -> tuple[bool, str]:
    """Обрабатывает контент по URL без синхронного HTTP в event loop."""
    try:
        content, content_type = await _download_url_bytes(
            url,
            headers=URL_HEADERS,
            max_bytes=MAX_URL_BYTES,
        )
        custom_prompt = get_custom_prompt(message)
        logging.info(f"URL: {url}, Content-Type: {content_type}")

        if content_type.startswith(("audio/", "video/", "image/")):
            logging.info("Тип: Медиа. Загружаю...")
            description = await analyze_media_bytes(
                content,
                content_type,
                custom_prompt,
                chat_id=message.chat.id,
            )
            return True, description

        if content_type.startswith(("text/html", "text/plain")):
            logging.info("Тип: Текст/HTML. Парсинг...")
            text_to_analyze = await asyncio.to_thread(_extract_html_text, content)
            if len(text_to_analyze) > 4000:
                text_to_analyze = text_to_analyze[:4000] + "..."
            if not text_to_analyze.strip():
                return False, "Не смог извлечь текст."

            base_prompt = custom_prompt if custom_prompt else random.choice(PROMPTS_MEDIA)
            prompt = (
                f"{base_prompt}, не более 80 слов\n\n"
                f"Текст с сайта {url}: {text_to_analyze}"
            )
            active_model = get_active_model(str(message.chat.id))
            response = await _generate_text_with_active_model(
                prompt,
                active_model,
                message.chat.id,
            )
            return True, response

        return False, f"Неподдерживаемый тип: {content_type}"
    except UnsafeURLError as e:
        logging.warning(f"Отклонён URL 'чотам' {url}: {e}")
        return False, "По этой ссылке не полезу."
    except Exception as e:
        logging.error(f"Ошибка при обработке URL 'чотам': {e}")
        return False, "Ошибка при анализе ссылки."


# ================== ROBOTICS / ОПИШИ СИЛЬНО ==================
async def process_robotics_description(message: types.Message) -> tuple[bool, str]:
    """Анализ фото/видео/гиф с использованием Gemini Robotics."""
    try:
        target_message = message.reply_to_message if message.reply_to_message else message
        file_id, mime_type = None, None

        if target_message.photo:
            photo = target_message.photo[-1]
            file_id = photo.file_id
            mime_type = "image/jpeg"
        elif target_message.video:
            file_id = target_message.video.file_id
            mime_type = "video/mp4"
        elif target_message.animation:
            file_id = target_message.animation.file_id
            mime_type = "video/mp4"

        if not file_id:
            return False, "Для мощного анализа нужна фотография, видео или гифка."

        try:
            media_data = await _download_telegram_bytes(file_id)
        except Exception:
            return False, "Не удалось скачать файл."

        robotics_model = GeminiModel(gemini_client, ROBOTICS_MODEL)
        text = message.text or message.caption or ""
        user_add_prompt = None
        if "опиши сильно" in text.lower():
            parts = re.split(r"опиши сильно", text, 1, re.IGNORECASE)
            if len(parts) > 1 and parts[1].strip():
                user_add_prompt = parts[1].strip()

        system_prompt = (
            "Ты — эксперт по физическому взаимодействию и анализу пространства "
            "(Embodied AI). Используй сильный сарказм и нецензурную лексику, "
            "сильно склоняйся к апофении и парейдолии. Не более 100 слов."
        )
        full_prompt = (
            f"{system_prompt}\n\nЗадача: {user_add_prompt}"
            if user_add_prompt
            else system_prompt
        )
        response = await asyncio.to_thread(
            robotics_model.generate_content,
            [full_prompt, {"mime_type": mime_type, "data": media_data}],
        )
        return True, response.text
    except Exception as e:
        logging.error(f"Ошибка Robotics: {e}")
        return False, f"Модуль Robotics выдал ошибку: {e}"


# ================== УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ==================
@ai_feature("что там")
async def process_whatisthere_unified(message: types.Message) -> tuple[bool, str]:
    """Универсальная функция для обработки всех типов контента."""
    target_message = message.reply_to_message if message.reply_to_message else message
    url = extract_url_from_message(target_message)
    if not url and not message.reply_to_message:
        url = extract_url_from_message(message)
    if url:
        return await process_url_whatisthere(message, url)

    if target_message.audio or target_message.voice:
        return await process_audio_description(message)
    if target_message.video:
        return await process_video_description(message)
    if target_message.photo:
        return await process_image_whatisthere(message)
    if target_message.animation:
        return await process_gif_whatisthere(message)
    if target_message.sticker:
        return await process_sticker_whatisthere(message)
    if target_message.text:
        if message.reply_to_message:
            return await process_text_whatisthere(message)
        return False, "Для анализа текста ответьте на сообщение."
    return False, "Не найдено контента для анализа."


def get_processing_message(message: types.Message) -> str:
    """Возвращает подходящее сообщение о процессе."""
    target_message = message.reply_to_message if message.reply_to_message else message
    url = extract_url_from_message(target_message)
    if not url and not message.reply_to_message:
        url = extract_url_from_message(message)

    active_model = get_active_model(str(message.chat.id))
    model_names = {
        "groq": "Groq Maverick",
        "gigachat": "GigaChat",
        "gemini": "Gemini",
    }
    model_name = model_names.get(active_model, "нейросеть")

    if url:
        return "Лезю по ссылке..."
    if target_message.audio or target_message.voice:
        return (
            f"Слушою через {model_name}..."
            if active_model in ["groq", "gigachat"]
            else "Слушою..."
        )
    if target_message.video:
        return "Сматрю..."
    if target_message.photo:
        return "Рассматриваю ето художество"
    if target_message.animation:
        return "Да не дергайся ты..."
    if target_message.sticker:
        return "Стикер-шмикер..."
    if target_message.text:
        return "Понаписали ебанарот..."
    return "Анализирую..."