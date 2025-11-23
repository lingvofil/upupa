import os
import random
import logging
import requests
from aiogram import types
from aiogram.types import FSInputFile
from playwright.async_api import async_playwright

# =============================================================================
# НОВАЯ ФУНКЦИЯ-ОРКЕСТРАТОР
# =============================================================================
async def process_channel_command(message: types.Message, channel_settings: dict):
    """
    Главная функция для обработки команд, связанных с каналами.
    Она подготавливает данные и вызывает основную логику.
    """
    command = message.text.lower()
    channel_info = channel_settings[command]
    
    # Логика подготовки данных, которая раньше была в хэндлере
    # Теперь она полностью скрыта внутри этого модуля
    if command == "каламбур" and "urls" in channel_info:
        # Если это "каламбур", выбираем случайный URL из списка
        url = random.choice(channel_info["urls"])
        # Собираем "универсальный" словарь для следующей функции
        prepared_info = {
            "url": url,
            "reply_message": channel_info["reply_message"],
            "error_message": channel_info["error_message"]
        }
        logging.info(f"Обрабатываем команду '{command}'. Выбранный URL: {url}")
    else:
        # Для всех остальных команд просто используем исходные данные
        prepared_info = channel_info
        logging.info(f"Обрабатываем команду '{command}'. URL: {prepared_info.get('url')}")
    
    # Вызываем основную функцию с уже подготовленными данными
    await _process_random_media(message, prepared_info)


async def _process_random_media(message: types.Message, channel_info: dict) -> bool:
    """
    Обрабатывает запрос на отправку случайного медиафайла.
    (Переименована в _process_random_media, чтобы показать, что она внутренняя)
    
    Args:
        message: Объект сообщения
        channel_info: ПОДГОТОВЛЕННАЯ информация о канале и настройках
    
    Returns:
        bool: True если медиафайл успешно отправлен, False в случае ошибки
    """
    max_attempts = 3
    attempt = 0
    
    await message.reply(channel_info["reply_message"])
    
    while attempt < max_attempts:
        try:
            # Теперь функция всегда получает 'url' в channel_info
            media_item = await _download_random_media(channel_info["url"])
            await _send_media_file(message, media_item['url'], media_item['type'])
            logging.info(f"Медиафайл успешно отправлен: {media_item['type']}")
            return True
            
        except Exception as e:
            attempt += 1
            if attempt >= max_attempts:
                logging.error(f"Ошибка при загрузке или отправке медиафайла: {e}")
                await message.reply(f"{channel_info['error_message']}. Ошибка: {str(e)}")
                return False
            else:
                logging.warning(f"Попытка {attempt} не удалась, пробуем еще раз...")
                continue

# Вспомогательные функции переименованы с подчеркиванием для ясности
async def _download_random_media(url):
    """Downloads a random media file from a given URL."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-features=IsolateOrigins,site-per-process',
                ]
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
            )
            page = await context.new_page()
            try:
                # ИЗМЕНЕНИЕ 1: Увеличен таймаут до 60 сек и изменено условие ожидания на domcontentloaded
                # Это позволяет не ждать загрузки всей рекламы и счетчиков
                await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                
                # ИЗМЕНЕНИЕ 2: Ожидание networkidle обернуто в try/except
                # Tgstat может постоянно подгружать данные, из-за чего networkidle никогда не наступит
                try:
                    await page.wait_for_load_state('networkidle', timeout=5000)
                except Exception:
                    pass # Игнорируем, если сеть не успокоилась, у нас есть явный wait ниже

                # Оставляем явное ожидание для подгрузки картинок (infinite scroll / lazy load)
                await page.wait_for_timeout(10000)
                
                media_urls = await page.evaluate("""
                    () => {
                        let media_sources = [];
                        document.querySelectorAll('video source, video').forEach(source => {
                            if (source.src || source.currentSrc) {
                                media_sources.push({ type: 'video', url: source.src || source.currentSrc });
                            }
                        });
                        document.querySelectorAll('img').forEach(img => {
                            if (img.src && !img.src.includes('placeholder') && img.naturalWidth > 640 && img.naturalHeight > 640) {
                                media_sources.push({ type: 'image', url: img.src, width: img.naturalWidth, height: img.naturalHeight });
                            }
                        });
                        return media_sources;
                    }
                """)
                
                if not media_urls:
                    raise Exception("Не найдено ни одного подходящего медиафайла.")
                
                return random.choice(media_urls)
            finally:
                await context.close()
                await browser.close()
    except Exception as e:
        logging.error(f"Ошибка при получении медиафайла: {str(e)}")
        raise

async def _send_media_file(message, media_url, media_type):
    """Sends a media file to the chat."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
    }
    try:
        response = requests.get(media_url, headers=headers, stream=True)
        response.raise_for_status()
        
        file_extension = 'mp4' if media_type == 'video' else 'jpg'
        file_name = f"temp_media.{file_extension}"
        
        with open(file_name, 'wb') as file:
            file.write(response.content)
        
        media = FSInputFile(file_name)
        
        if media_type == 'video':
            await message.answer_video(media)
        else:
            await message.answer_photo(media)
            
        os.remove(file_name)
    except Exception as e:
        logging.error(f"Ошибка при отправке медиафайла: {str(e)}")
        raise
