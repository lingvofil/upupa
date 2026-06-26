# === AI/videogeneration.py — генерация видео через Pollinations ===
#
# API: GET https://gen.pollinations.ai/video/{prompt}  -> video/mp4
#   model: veo | seedance | seedance-2.0 | wan | wan-fast | ...
#   image[0] = стартовый кадр (I2V), image[1] = конечный кадр (поддерживают
#   veo, seedance, seedance-2.0, wan-fast). Кадры передаются как URL —
#   байты сначала загружаются через POST /upload (хранится 30 дней).
#   429/503 -> бэкофф по Retry-After.
#
# Команды (см. handlers/video.py):
#   "упупа сними <промпт>"            — текст -> видео
#   "упупа сними <промпт>" реплаем
#       на фото                       — фото как стартовый кадр
#   "оживи [промпт]" реплаем на фото  — анимация фото

import asyncio
import logging
import time
import urllib.parse
from datetime import date

import aiohttp
from aiogram import types

from core.settings import POLLINATIONS_API_KEY
from AI.adddescribe import download_telegram_image

BASE_URL = "https://gen.pollinations.ai"

# Предпочтительный порядок моделей (дешёвые/бесплатные сначала).
# Реальный список берём из живого каталога /image/models — он у Pollinations
# динамический, имена моделей появляются и исчезают без предупреждения.
# Порядок — по фактической цене в поллене (из ответов API, июнь 2026):
# ltx-2 ~0.025 | wan-fast 0.063 | p-video-720p 0.10 | seedance-pro 0.16
# p-video-1080p 0.20 | wan 0.61 | veo 0.97 | seedance-2.0 0.99
VIDEO_MODEL_PREFERENCE = [
    "ltx-2",
    "wan-fast",
    "p-video-720p",
    "seedance-pro",
    "p-video-1080p",
    "wan",
    "seedance", "seedance-2.0",
    "veo",
]
_FALLBACK_QUEUE = ["ltx-2", "wan-fast", "p-video-720p"]

_models_cache: dict = {"ts": 0.0, "queue": None}
_MODELS_CACHE_TTL = 3600

VIDEO_DURATION_SECONDS = 5
VIDEO_TIMEOUT_SECONDS = 420       # генерация видео идёт десятки секунд — минуты
DAILY_LIMIT_PER_CHAT = 3          # бережём бесплатные гранты Pollen
DAILY_LIMIT_GLOBAL = 10           # суммарно по всем чатам в день

# {(isodate, chat_id): count} — сбрасывается сменой даты, потеря при рестарте ок
_usage: dict = {}


def _check_and_count_limit(chat_id: int) -> bool:
    today = date.today().isoformat()
    # подчистка старых дат
    for k in [k for k in _usage if k[0] != today]:
        del _usage[k]
    if sum(_usage.values()) >= DAILY_LIMIT_GLOBAL:
        return False
    key = (today, chat_id)
    if _usage.get(key, 0) >= DAILY_LIMIT_PER_CHAT:
        return False
    _usage[key] = _usage.get(key, 0) + 1
    return True


def _headers() -> dict:
    return {"Authorization": f"Bearer {POLLINATIONS_API_KEY}"}


def _order_models(names: list) -> list:
    """Сортирует имена видео-моделей по VIDEO_MODEL_PREFERENCE, незнакомые — в конец."""
    known = [m for m in VIDEO_MODEL_PREFERENCE if m in names]
    unknown = [n for n in names if n not in VIDEO_MODEL_PREFERENCE]
    return known + unknown


def _extract_video_models(catalog) -> list:
    """Достаёт имена видео-моделей из каталога, форма которого может меняться."""
    names = []
    items = catalog if isinstance(catalog, list) else catalog.get("models", []) if isinstance(catalog, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("id") or ""
        is_video = (
            item.get("video") is True
            or "video" in (item.get("output_modalities") or [])
            or "video_capabilities" in item
            or item.get("type") == "video"
        )
        if name and is_video:
            names.append(name)
    return names


async def get_video_model_queue() -> list:
    """Очередь видео-моделей из живого каталога (кэш 1 час), при сбое — статическая."""
    now = time.time()
    if _models_cache["queue"] and now - _models_cache["ts"] < _MODELS_CACHE_TTL:
        return _models_cache["queue"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BASE_URL}/image/models",
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    names = _extract_video_models(await resp.json())
                    if names:
                        queue = _order_models(names)
                        _models_cache.update(ts=now, queue=queue)
                        logging.info(f"Видео-модели из каталога: {queue}")
                        return queue
                logging.warning(f"Каталог моделей: HTTP {resp.status}")
    except Exception as e:
        logging.warning(f"Каталог моделей недоступен: {e}")
    return _models_cache["queue"] or _FALLBACK_QUEUE


async def upload_media(data: bytes, filename: str = "frame.jpg") -> str | None:
    """Загружает байты в content-addressed store Pollinations, возвращает URL."""
    try:
        endpoints = (
            "https://media.pollinations.ai/upload",  # рабочий (июнь 2026)
            f"{BASE_URL}/upload",                     # запасные, если основной переедет
            f"{BASE_URL}/v1/upload",
        )
        async with aiohttp.ClientSession() as session:
            for url in endpoints:
                form = aiohttp.FormData()
                form.add_field("file", data, filename=filename, content_type="image/jpeg")
                async with session.post(
                    url, headers=_headers(), data=form,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 200:
                        payload = await resp.json()
                        media_url = payload.get("url")
                        if media_url:
                            logging.info(f"Кадр загружен через {url}")
                            return media_url
                    body = (await resp.text())[:200]
                    # пока работает основной адрес, провал запасных — не повод шуметь
                    logging.debug(f"Pollinations upload {url}: HTTP {resp.status}: {body}")
        logging.error("Pollinations upload: все адреса недоступны")
        return None
    except Exception as e:
        logging.error(f"Pollinations upload error: {e}")
        return None


async def generate_video(
    prompt: str,
    start_frame_url: str | None = None,
    duration: int = VIDEO_DURATION_SECONDS,
) -> tuple[bytes | None, str | None]:
    """Генерирует видео, перебирая очередь моделей.

    Возвращает (mp4, имя модели); (None, "no_pollen") — если всё упёрлось
    в пустой баланс Pollen (HTTP 402).
    """
    encoded = urllib.parse.quote(prompt[:500])
    model_queue = await get_video_model_queue()
    saw_402 = 0
    params_base = {"duration": str(duration), "aspectRatio": "16:9"}
    if start_frame_url:
        params_base["image"] = start_frame_url

    timeout = aiohttp.ClientTimeout(total=VIDEO_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model in model_queue:
            params = dict(params_base, model=model)
            try:
                async with session.get(
                    f"{BASE_URL}/video/{encoded}", headers=_headers(), params=params,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        logging.info(f"Видео сгенерировано: model={model}, {len(data)} байт")
                        return data, model
                    body = (await resp.text())[:300]
                    logging.warning(f"Видео {model}: HTTP {resp.status}: {body}")
                    if resp.status == 402:
                        saw_402 += 1
                        continue
                    if resp.status in (429, 503):
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit() and int(retry_after) <= 60:
                            await asyncio.sleep(int(retry_after))
            except asyncio.TimeoutError:
                logging.warning(f"Видео {model}: таймаут {VIDEO_TIMEOUT_SECONDS}с")
            except Exception as e:
                logging.error(f"Видео {model}: {e}")
    if saw_402 and saw_402 == len(model_queue):
        return None, "no_pollen"
    return None, None


def _extract_prompt(text: str, *triggers: str) -> str:
    low = text.lower()
    for t in triggers:
        if low.startswith(t):
            return text[len(t):].strip()
    return text.strip()


async def process_video_generation(message: types.Message, bot) -> None:
    """Команда 'упупа сними <промпт>' (+ опционально реплай на фото = стартовый кадр)."""
    prompt = _extract_prompt(message.text or "", "упупа сними", "упупа, сними")
    reply_photo = message.reply_to_message.photo if (
        message.reply_to_message and message.reply_to_message.photo) else None

    if not prompt and not reply_photo:
        await message.reply("Что снимать-то? Напиши: упупа сними <описание>")
        return
    if not _check_and_count_limit(message.chat.id):
        await message.reply(f"🎬 Лимит видео на сегодня исчерпан ({DAILY_LIMIT_PER_CHAT}/день на чат).")
        return

    status = await message.reply("🎬 Снимаю... минуту-другую")
    try:
        start_url = None
        if reply_photo:
            img = await download_telegram_image(bot, reply_photo[-1])
            if img:
                start_url = await upload_media(img)
        video, model = await generate_video(prompt or "cinematic scene", start_frame_url=start_url)
        if not video:
            if model == "no_pollen":
                await status.edit_text(
                    "🎬 Кончилось топливо: на балансе Pollinations ноль поллена.\n"
                    "Админу нужно заглянуть в enter.pollinations.ai."
                )
            else:
                await status.edit_text("Не вышло снять. Все видео-модели отказали, попробуй позже.")
            return
        await message.reply_video(
            types.BufferedInputFile(video, filename="upupa_video.mp4"),
        )
        await status.delete()
    except Exception as e:
        logging.error(f"process_video_generation: {e}", exc_info=True)
        try:
            await status.edit_text("Сломался на съёмках. Попробуй ещё раз.")
        except Exception:
            pass


async def process_animate_photo(message: types.Message, bot) -> None:
    """Команда 'оживи [промпт]' реплаем на фото — image-to-video."""
    if not (message.reply_to_message and message.reply_to_message.photo):
        await message.reply("Оживить можно только фото — ответь командой на картинку.")
        return
    if not _check_and_count_limit(message.chat.id):
        await message.reply(f"🎬 Лимит видео на сегодня исчерпан ({DAILY_LIMIT_PER_CHAT}/день на чат).")
        return

    prompt = _extract_prompt(message.text or "", "оживи")
    status = await message.reply("🧟 Оживляю...")
    try:
        img = await download_telegram_image(bot, message.reply_to_message.photo[-1])
        if not img:
            await status.edit_text("Не смог скачать фото.")
            return
        start_url = await upload_media(img)
        if not start_url:
            await status.edit_text("Не смог загрузить кадр, попробуй позже.")
            return
        video, model = await generate_video(
            prompt or "bring this image to life, natural motion",
            start_frame_url=start_url,
        )
        if not video:
            if model == "no_pollen":
                await status.edit_text(
                    "🎬 Кончилось топливо: на балансе Pollinations ноль поллена.\n"
                    "Админу нужно заглянуть в enter.pollinations.ai."
                )
            else:
                await status.edit_text("Оживить не вышло. Все видео-модели отказали, попробуй позже.")
            return
        await message.reply_video(
            types.BufferedInputFile(video, filename="upupa_alive.mp4"),
        )
        await status.delete()
    except Exception as e:
        logging.error(f"process_animate_photo: {e}", exc_info=True)
        try:
            await status.edit_text("Сломался при оживлении. Попробуй ещё раз.")
        except Exception:
            pass
