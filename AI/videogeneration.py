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
import urllib.parse
from datetime import date

import aiohttp
from aiogram import types

from core.settings import POLLINATIONS_API_KEY
from AI.adddescribe import download_telegram_image

BASE_URL = "https://gen.pollinations.ai"

# Очередь моделей: первая успешная побеждает
VIDEO_MODEL_QUEUE = ["seedance", "wan-fast", "veo"]

VIDEO_DURATION_SECONDS = 5
VIDEO_TIMEOUT_SECONDS = 420       # генерация видео идёт десятки секунд — минуты
DAILY_LIMIT_PER_CHAT = 5          # бережём бесплатные гранты Pollen

# {(isodate, chat_id): count} — сбрасывается сменой даты, потеря при рестарте ок
_usage: dict = {}


def _check_and_count_limit(chat_id: int) -> bool:
    key = (date.today().isoformat(), chat_id)
    if _usage.get(key, 0) >= DAILY_LIMIT_PER_CHAT:
        return False
    _usage[key] = _usage.get(key, 0) + 1
    # подчистка старых дат
    today = date.today().isoformat()
    for k in [k for k in _usage if k[0] != today]:
        del _usage[k]
    return True


def _headers() -> dict:
    return {"Authorization": f"Bearer {POLLINATIONS_API_KEY}"}


async def upload_media(data: bytes, filename: str = "frame.jpg") -> str | None:
    """Загружает байты в content-addressed store Pollinations, возвращает URL."""
    try:
        form = aiohttp.FormData()
        form.add_field("file", data, filename=filename, content_type="image/jpeg")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BASE_URL}/upload", headers=_headers(), data=form,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    logging.error(f"Pollinations upload: HTTP {resp.status}: {await resp.text()}")
                    return None
                payload = await resp.json()
                return payload.get("url")
    except Exception as e:
        logging.error(f"Pollinations upload error: {e}")
        return None


async def generate_video(
    prompt: str,
    start_frame_url: str | None = None,
    duration: int = VIDEO_DURATION_SECONDS,
) -> tuple[bytes | None, str | None]:
    """Генерирует видео, перебирая очередь моделей. Возвращает (mp4, имя модели)."""
    encoded = urllib.parse.quote(prompt[:500])
    params_base = {"duration": str(duration), "aspectRatio": "16:9"}
    if start_frame_url:
        params_base["image"] = start_frame_url

    timeout = aiohttp.ClientTimeout(total=VIDEO_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for model in VIDEO_MODEL_QUEUE:
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
                    if resp.status in (429, 503):
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit() and int(retry_after) <= 60:
                            await asyncio.sleep(int(retry_after))
            except asyncio.TimeoutError:
                logging.warning(f"Видео {model}: таймаут {VIDEO_TIMEOUT_SECONDS}с")
            except Exception as e:
                logging.error(f"Видео {model}: {e}")
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
            await status.edit_text("Не вышло снять. Все видео-модели отказали, попробуй позже.")
            return
        await message.reply_video(
            types.BufferedInputFile(video, filename="upupa_video.mp4"),
            caption=f"🎬 {model}",
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
            await status.edit_text("Оживить не вышло. Все видео-модели отказали, попробуй позже.")
            return
        await message.reply_video(
            types.BufferedInputFile(video, filename="upupa_alive.mp4"),
            caption=f"🧟 {model}",
        )
        await status.delete()
    except Exception as e:
        logging.error(f"process_animate_photo: {e}", exc_info=True)
        try:
            await status.edit_text("Сломался при оживлении. Попробуй ещё раз.")
        except Exception:
            pass
