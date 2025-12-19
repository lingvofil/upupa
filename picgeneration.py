import asyncio
import base64
import json
import logging
import os
import random
import textwrap
import time
from io import BytesIO
from typing import Optional, Tuple, Union

import requests
from PIL import Image, ImageDraw, ImageFont
from aiogram import types
from aiogram.exceptions import TelegramBadRequest

import config
from config import bot, model, KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY, API_TOKEN
from prompts import actions
from adddescribe import download_telegram_image

# Безопасное получение ключей из конфига
CF_ACCOUNT_ID = getattr(config, 'CLOUDFLARE_ACCOUNT_ID', None)
CF_API_TOKEN = getattr(config, 'CLOUDFLARE_API_TOKEN', None)
HF_TOKEN = getattr(config, 'HUGGINGFACE_TOKEN', None)

# =============================================================================
# КЛАСС KANDINSKY (FUSIONBRAIN)
# =============================================================================

class FusionBrainAPI:
    def __init__(self, url: str, api_key: str, secret_key: str):
        self.URL = url
        self.headers = {
            'X-Key': f'Key {api_key}',
            'X-Secret': f'Secret {secret_key}',
        }

    def get_pipeline(self) -> Optional[str]:
        try:
            r = requests.get(self.URL + 'key/api/v1/pipelines', headers=self.headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            return data[0]['id'] if data else None
        except Exception as e:
            logging.error(f"Kandinsky pipeline error: {e}")
            return None

    def generate(self, prompt: str, pipeline_id: str) -> Tuple[Optional[str], Optional[str]]:
        params = {
            "type": "GENERATE",
            "numImages": 1,
            "width": 1024,
            "height": 1024,
            "generateParams": {"query": prompt[:900]},
        }
        data = {
            'pipeline_id': (None, pipeline_id),
            'params': (None, json.dumps(params), 'application/json'),
        }
        try:
            r = requests.post(self.URL + 'key/api/v1/pipeline/run', headers=self.headers, files=data, timeout=20)
            r.raise_for_status()
            res = r.json()
            return res.get('uuid'), None
        except Exception as e:
            return None, str(e)

    def check(self, uuid: str) -> Tuple[Optional[bytes], Optional[str]]:
        for _ in range(15):
            try:
                r = requests.get(self.URL + f'key/api/v1/pipeline/status/{uuid}', headers=self.headers, timeout=15)
                r.raise_for_status()
                data = r.json()
                if data.get('status') == 'DONE':
                    if data.get('result', {}).get('censored'):
                        return None, "Censored"
                    img_b64 = data['result']['files'][0]
                    return base64.b64decode(img_b64.split(',')[-1]), None
                if data.get('status') == 'FAIL':
                    return None, data.get('errorDescription', 'Unknown fail')
                time.sleep(5)
            except Exception as e:
                return None, str(e)
        return None, "Timeout"

kandinsky_api = FusionBrainAPI('https://api-key.fusionbrain.ai/', KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY)
# Кэшируем pipeline_id при запуске
PIPELINE_ID = kandinsky_api.get_pipeline()

# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

async def translate_to_en(text: str) -> str:
    """Перевод промпта на английский через Gemini."""
    if not text: return ""
    try:
        res = await asyncio.to_thread(lambda: model.generate_content(
            f"Translate to English for image generation prompt. Output only translation: {text}"
        ).text)
        return res.strip()
    except Exception:
        return text

def _overlay_text_on_image(image_bytes: bytes, text: str) -> str:
    """Наложение текста на изображение (для каламбуров)."""
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not os.path.exists(font_path): font_path = "arial.ttf"
    try: font = ImageFont.truetype(font_path, 48)
    except: font = ImageFont.load_default()

    lines = textwrap.wrap(text, width=20)
    line_height = 55
    text_block_h = line_height * len(lines)
    y_start = image.height - text_block_h - 60

    # Подложка
    rect = Image.new('RGBA', (image.width, text_block_h + 40), (0, 0, 0, 140))
    image.paste(rect, (0, y_start - 20), rect)

    curr_y = y_start - 10
    for line in lines:
        try: w = font.getbbox(line)[2]
        except: w = len(line) * 20
        x = (image.width - w) / 2
        draw.text((x, curr_y), line, font=font, fill="white", stroke_width=2, stroke_fill="black")
        curr_y += line_height

    out_path = f"pun_{random.randint(1000,9999)}.jpg"
    image.save(out_path, quality=95)
    return out_path

async def send_generated_photo(message: types.Message, data: bytes, filename: str):
    """Отправка байтов как фото в Telegram."""
    try:
        input_file = types.BufferedInputFile(data, filename=filename)
        await message.reply_photo(input_file)
    except Exception as e:
        logging.error(f"Send photo error: {e}")
        await message.reply("Не удалось отправить файл.")

# =============================================================================
# ГЕНЕРАТОРЫ (HF & CF)
# =============================================================================

async def hf_generate(prompt: str, model_id: str) -> Optional[bytes]:
    if not HF_TOKEN: return None
    url = f"https://router.huggingface.co/hf-inference/models/{model_id}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Accept": "image/png"}
    payload = {"inputs": prompt, "options": {"wait_for_model": True, "use_cache": False}}
    try:
        r = await asyncio.to_thread(lambda: requests.post(url, headers=headers, json=payload, timeout=120))
        return r.content if r.status_code == 200 else None
    except: return None

async def cf_generate_t2i(prompt: str) -> Optional[bytes]:
    if not CF_ACCOUNT_ID or not CF_API_TOKEN: return None
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/stabilityai/stable-diffusion-xl-base-1.0"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    try:
        r = await asyncio.to_thread(lambda: requests.post(url, headers=headers, json={"prompt": prompt}, timeout=60))
        return r.content if r.status_code == 200 else None
    except: return None

# =============================================================================
# ГЛАВНЫЙ ОРКЕСТРАТОР
# =============================================================================

async def robust_image_generation(message: types.Message, prompt_ru: str, processing_msg: types.Message):
    """Цепочка: Kandinsky -> FLUX Schnell -> FLUX Dev -> SDXL HF -> SDXL CF."""
    global PIPELINE_ID
    
    # 1. Kandinsky
    if not PIPELINE_ID: PIPELINE_ID = kandinsky_api.get_pipeline()
    if PIPELINE_ID:
        uuid, err = kandinsky_api.generate(prompt_ru, PIPELINE_ID)
        if uuid:
            img, check_err = await asyncio.to_thread(kandinsky_api.check, uuid)
            if img:
                await processing_msg.delete()
                await send_generated_photo(message, img, "kandinsky.png")
                return

    # Перевод для остальных моделей
    await processing_msg.edit_text("Кандинский не смог, перевожу промпт...")
    prompt_en = await translate_to_en(prompt_ru)

    # 2-4. HuggingFace Chain
    hf_models = [
        'black-forest-labs/FLUX.1-schnell',
        'black-forest-labs/FLUX.1-dev',
        'stabilityai/stable-diffusion-xl-base-1.0'
    ]
    
    for m_id in hf_models:
        await processing_msg.edit_text(f"Пробую {m_id.split('/')[-1]}...")
        img = await hf_generate(prompt_en, m_id)
        if img:
            await processing_msg.delete()
            await send_generated_photo(message, img, f"{m_id.replace('/','_')}.png")
            return

    # 5. Cloudflare Fallback
    await processing_msg.edit_text("Последний шанс (Cloudflare)...")
    img = await cf_generate_t2i(prompt_en)
    if img:
        await processing_msg.delete()
        await send_generated_photo(message, img, "cloudflare.png")
        return

    await processing_msg.edit_text("Ни одна модель не справилась. Попробуй позже.")

# =============================================================================
# ХЭНДЛЕРЫ ДЛЯ main.py
# =============================================================================

async def handle_image_generation_command(message: types.Message):
    """Стандартная команда 'нарисуй'."""
    prompt = message.text.lower().replace("нарисуй", "").strip()
    if not prompt and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    
    if not prompt:
        return await message.reply("Что рисовать-то?")
    
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    msg = await message.reply("Ща, краски разведу...")
    full_prompt = f"{prompt}, high quality, highly detailed"
    await robust_image_generation(message, full_prompt, msg)

async def handle_pun_image_command(message: types.Message):
    """Генерация каламбура с картинкой."""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    msg = await message.reply("Придумываю каламбур...")
    
    pun_query = "Придумай смешной каламбур-склейку двух слов. Формат: слово1+слово2 = итоговоеслово. Только одну строку."
    try:
        pun_res = await asyncio.to_thread(lambda: model.generate_content(pun_query).text.strip())
        if '=' not in pun_res: 
            return await msg.edit_text(f"Не вышло: {pun_res}")
        
        source, final_word = pun_res.split('=')[0].strip(), pun_res.split('=')[1].strip()
        img_prompt = f"Surrealistic visual of {final_word} (combination of {source}). Photo style, no text."
        
        # Для каламбуров сразу идем в robust (начиная с Кандинского)
        # Но сначала попробуем получить данные через robust_image_generation (нужно чуть переделать логику возврата)
        # Для упрощения здесь используем упрощенный waterfall
        img_data = await hf_generate(await translate_to_en(img_prompt), "black-forest-labs/FLUX.1-schnell")
        if not img_data:
            success, err, img_data = await asyncio.to_thread(kandinsky_api.generate, img_prompt, PIPELINE_ID)
            # ... здесь можно добавить полный waterfall, но для краткости:
        
        if img_data:
            path = await asyncio.to_thread(_overlay_text_on_image, img_data, final_word)
            await message.reply_photo(types.FSInputFile(path))
            os.remove(path)
            await msg.delete()
        else:
            await msg.edit_text("Каламбур придумал, а рисовать лень.")
    except Exception as e:
        await msg.edit_text(f"Ошибка каламбура: {e}")

async def handle_redraw_command(message: types.Message):
    """Перерисовка ('мазня')."""
    photo = message.photo[-1] if message.photo else None
    if not photo and message.reply_to_message and message.reply_to_message.photo:
        photo = message.reply_to_message.photo[-1]
    
    if not photo:
        return await message.reply("Где картинка?")

    msg = await message.reply("Анализирую этот шедевр...")
    try:
        img_bytes = await download_telegram_image(bot, photo)
        desc_res = await asyncio.to_thread(lambda: model.generate_content([
            "Опиши кратко что на картинке для генерации в стиле детского рисунка.", 
            {"mime_type": "image/jpeg", "data": img_bytes}
        ]))
        prompt = f"Childish crayon drawing, naive art, {desc_res.text.strip()}"
        await robust_image_generation(message, prompt, msg)
    except Exception:
        await msg.edit_text("Не смог рассмотреть.")

async def handle_edit_command(message: types.Message):
    """Редактирование через Img2Img (Cloudflare)."""
    photo = message.photo[-1] if message.photo else None
    if not photo and message.reply_to_message and message.reply_to_message.photo:
        photo = message.reply_to_message.photo[-1]
    
    if not photo: return await message.reply("Нужно фото.")
    
    prompt_text = (message.caption or message.text or "").lower().replace("отредактируй", "").strip()
    if not prompt_text: return await message.reply("Во что превращаем?")

    msg = await message.reply("Ща подкрасим...")
    try:
        img_bytes = await download_telegram_image(bot, photo)
        en_prompt = await translate_to_en(prompt_text)
        
        # Img2Img специфичен для CF
        url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/runwayml/stable-diffusion-v1-5-img2img"
        headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
        
        # Ресайз для SD 1.5
        img = Image.open(BytesIO(img_bytes)).convert("RGB").resize((512, 512))
        buf = BytesIO(); img.save(buf, format="PNG"); final_bytes = buf.getvalue()
        
        payload = {"prompt": en_prompt, "image": list(final_bytes), "strength": 0.6}
        r = await asyncio.to_thread(lambda: requests.post(url, headers=headers, json=payload, timeout=60))
        
        if r.status_code == 200:
            await msg.delete()
            await send_generated_photo(message, r.content, "edited.png")
        else:
            await msg.edit_text("Не вышло отредактировать.")
    except Exception as e:
        logging.error(f"Edit error: {e}")
        await msg.edit_text("Ошибка API.")
