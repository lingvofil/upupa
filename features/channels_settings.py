import random
import logging
from urllib.parse import urlparse
from aiogram import types
from aiogram.types import BufferedInputFile
from playwright.async_api import async_playwright

from infrastructure.media_io import download_url_bytes

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


def _extract_post_title(post_text):
    """Возвращает только первую непустую строку текста поста."""
    if not post_text:
        return None

    for line in str(post_text).splitlines():
        title = " ".join(line.split())
        if title:
            # Telegram ограничивает подпись к фото/видео 1024 символами.
            return title[:1024]
    return None


def _telegram_preview_url(url):
    """Построить URL публичной Telegram-ленты для страницы TGStat."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"tgstat.ru", "www.tgstat.ru"}:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] != "channel":
        return None

    username = parts[1].lstrip("@")
    if not username or any(not (char.isalnum() or char == "_") for char in username):
        return None

    return f"https://t.me/s/{username}"


def _media_source_urls(url):
    """Вернуть источники медиа в порядке приоритета: Telegram, затем TGStat."""
    telegram_url = _telegram_preview_url(url)
    if telegram_url:
        return [telegram_url, url]
    return [url]


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
            include_post_title = bool(channel_info.get("include_post_title"))
            media_item = await _download_random_media(
                channel_info["url"],
                include_post_title=include_post_title,
            )
            caption = (
                _extract_post_title(media_item.get("post_text"))
                if include_post_title
                else None
            )
            await _send_media_file(
                message,
                media_item['url'],
                media_item['type'],
                caption=caption,
            )
            logging.info(
                "Медиафайл успешно отправлен: %s; заголовок=%r",
                media_item['type'],
                caption,
            )
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
async def _download_random_media(url, include_post_title=False):
    """Скачать случайное медиа: сначала из Telegram, затем fallback через TGStat."""
    source_urls = _media_source_urls(url)

    last_error = None

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
                for source_url in source_urls:
                    try:
                        response = await page.goto(
                            source_url,
                            timeout=60000,
                            wait_until='domcontentloaded',
                        )
                        status = response.status if response is not None else None
                        if status is not None and status >= 400:
                            last_error = RuntimeError(
                                f"Источник {source_url} вернул HTTP {status}"
                            )
                            logging.warning(
                                "Источник медиа недоступен: url=%s status=%s",
                                source_url,
                                status,
                            )
                            continue

                        try:
                            await page.wait_for_load_state('networkidle', timeout=5000)
                        except Exception:
                            pass

                        # Даём lazy-load медиа отрисоваться и подгружаем нижнюю часть ленты.
                        await page.wait_for_timeout(3000)
                        await page.evaluate(
                            "window.scrollTo(0, document.body.scrollHeight)"
                        )
                        await page.wait_for_timeout(1500)

                        media_urls = await page.evaluate(
                            """
                            (includePostTitle) => {
                                const mediaSources = [];
                                const seen = new Set();
                                const textSelectors = [
                                    '.tgme_widget_message_text',
                                    '.post-text',
                                    '.post-description',
                                    '.post-content',
                                    '.post-body',
                                    '.card-text',
                                    '.message-text',
                                    '[class*="post-text"]',
                                    '[class*="post__text"]'
                                ];

                                const getPostText = (element) => {
                                    if (!includePostTitle || !element) {
                                        return null;
                                    }

                                    const telegramMessage = element.closest?.(
                                        '.tgme_widget_message'
                                    );
                                    const telegramText = telegramMessage
                                        ?.querySelector('.tgme_widget_message_text')
                                        ?.innerText?.trim();
                                    if (telegramText) {
                                        return telegramText;
                                    }

                                    let node = element;
                                    let fallback = null;
                                    for (
                                        let depth = 0;
                                        node && depth < 8;
                                        depth += 1, node = node.parentElement
                                    ) {
                                        for (const selector of textSelectors) {
                                            const candidate = node.matches?.(selector)
                                                ? node
                                                : node.querySelector?.(selector);
                                            const text = candidate?.innerText?.trim();
                                            if (text) {
                                                return text;
                                            }
                                        }

                                        const ancestorText = node.innerText?.trim();
                                        if (
                                            ancestorText
                                            && ancestorText.length <= 5000
                                        ) {
                                            fallback = ancestorText;
                                        }
                                    }
                                    return fallback;
                                };

                                const addMedia = (
                                    type,
                                    rawUrl,
                                    element,
                                    width = null,
                                    height = null
                                ) => {
                                    if (!rawUrl || rawUrl.includes('placeholder')) {
                                        return;
                                    }

                                    let absoluteUrl;
                                    try {
                                        absoluteUrl = new URL(
                                            rawUrl,
                                            document.baseURI
                                        ).href;
                                    } catch (_error) {
                                        return;
                                    }

                                    if (
                                        !/^https?:/i.test(absoluteUrl)
                                        || seen.has(absoluteUrl)
                                    ) {
                                        return;
                                    }

                                    seen.add(absoluteUrl);
                                    mediaSources.push({
                                        type,
                                        url: absoluteUrl,
                                        width,
                                        height,
                                        post_text: getPostText(element)
                                    });
                                };

                                const backgroundUrl = (element) => {
                                    const value = (
                                        element.style?.backgroundImage
                                        || window.getComputedStyle(element)
                                            ?.backgroundImage
                                        || ''
                                    ).trim();
                                    if (!value.startsWith('url(') || !value.endsWith(')')) {
                                        return null;
                                    }
                                    return value
                                        .slice(4, -1)
                                        .trim()
                                        .replace(/^["']|["']$/g, '');
                                };

                                document
                                    .querySelectorAll('video source, video')
                                    .forEach(source => {
                                        const video = source.closest('video') || source;
                                        const mediaUrl = (
                                            source.currentSrc
                                            || source.src
                                            || source.getAttribute?.('src')
                                            || source.dataset?.src
                                        );
                                        addMedia('video', mediaUrl, video);
                                    });

                                document.querySelectorAll('img').forEach(img => {
                                    const className = String(img.className || '');
                                    if (/emoji|avatar|icon|logo/i.test(className)) {
                                        return;
                                    }

                                    const width = img.naturalWidth || img.width || 0;
                                    const height = img.naturalHeight || img.height || 0;
                                    const inTelegramPost = Boolean(
                                        img.closest('.tgme_widget_message')
                                    );
                                    const largeImage = width > 640 && height > 640;
                                    const telegramPostImage = (
                                        inTelegramPost
                                        && width >= 320
                                        && height >= 180
                                    );

                                    if (!largeImage && !telegramPostImage) {
                                        return;
                                    }

                                    const mediaUrl = (
                                        img.currentSrc
                                        || img.src
                                        || img.dataset?.src
                                        || img.dataset?.original
                                    );
                                    addMedia(
                                        'image',
                                        mediaUrl,
                                        img,
                                        width,
                                        height
                                    );
                                });

                                document.querySelectorAll(
                                    [
                                        '.tgme_widget_message_photo_wrap',
                                        '.tgme_widget_message_service_photo',
                                        '[class*="post"] [style*="background-image"]'
                                    ].join(',')
                                ).forEach(element => {
                                    addMedia(
                                        'image',
                                        backgroundUrl(element),
                                        element
                                    );
                                });

                                return mediaSources;
                            }
                            """,
                            include_post_title,
                        )

                        if media_urls:
                            logging.info(
                                "Найдено медиа: source=%s count=%d",
                                source_url,
                                len(media_urls),
                            )
                            return random.choice(media_urls)

                        last_error = RuntimeError(
                            f"Источник {source_url} не содержит подходящего медиа"
                        )
                        logging.warning(
                            "Подходящее медиа не найдено: source=%s",
                            source_url,
                        )
                    except Exception as source_error:
                        last_error = source_error
                        logging.warning(
                            "Не удалось разобрать источник медиа %s: %s",
                            source_url,
                            source_error,
                        )

                error = Exception("Не найдено ни одного подходящего медиафайла.")
                if last_error is not None:
                    raise error from last_error
                raise error
            finally:
                await context.close()
                await browser.close()
    except Exception as e:
        logging.error(f"Ошибка при получении медиафайла: {str(e)}")
        raise

async def _send_media_file(message, media_url, media_type, caption=None):
    """Скачивает и отправляет медиа без блокировки event loop и temp-файлов."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
    }
    try:
        media_data = await download_url_bytes(media_url, headers=headers)
        file_extension = 'mp4' if media_type == 'video' else 'jpg'
        media = BufferedInputFile(
            media_data,
            filename=f"channel_media.{file_extension}",
        )

        if media_type == 'video':
            await message.answer_video(media, caption=caption)
        else:
            await message.answer_photo(media, caption=caption)
    except Exception as e:
        logging.error(f"Ошибка при отправке медиафайла: {str(e)}")
        raise
