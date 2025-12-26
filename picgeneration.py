#picgeneration.py

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

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
            # Используем небольшой таймаут, чтобы не вешать бота при 502 ошибке
            r = requests.get(self.URL + 'key/api/v1/pipelines', headers=self.headers, timeout=5)
            if r.status_code != 200:
                logging.warning(f"Kandinsky API недоступен (Status: {r.status_code})")
                return None
            data = r.json()
            return data[0]['id'] if data else None
        except Exception:
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
            r = requests.post(self.URL + 'key/api/v1/pipeline/run', headers=self.headers, files=data, timeout=10)
            r.raise_for_status()
            res = r.json()
            return res.get('uuid'), None
        except Exception as e:
            return None, str(e)

    def check(self, uuid: str) -> Tuple[Optional[bytes], Optional[str]]:
        for _ in range(10): # Уменьшили кол-во попыток для более быстрого фолбека
            try:
                r = requests.get(self.URL + f'key/api/v1/pipeline/status/{uuid}', headers=self.headers, timeout=10)
                r.raise_for_status()
                data = r.json()
                if data.get('status') == 'DONE':
                    if data.get('result', {}).get('censored'):
                        return None, "Censored"
                    img_b64 = data['result']['files'][0]
                    return base64.b64decode(img_b64.split(',')[-1]), None
                if data.get('status') == 'FAIL':
                    return None, data.get('errorDescription', 'Unknown fail')
                time.sleep(2)
            except Exception as e:
                return None, str(e)
        return None, "Timeout"

kandinsky_api = FusionBrainAPI('https://api-key.fusionbrain.ai/', KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY)
PIPELINE_ID = None

# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

async def translate_to_en(text: str) -> str:
    if not text: return ""
    try:
        res = await asyncio.to_thread(lambda: model.generate_content(
            f"Expand and translate this prompt for high-quality image generation in English. "
            f"Add descriptive keywords for artistic style. Output only the translated prompt: {text}"
        ).text)
        return res.strip()
    except Exception:
        return text

def _overlay_text_on_image(image_bytes: bytes, text: str) -> str:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    
    font_paths = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "arial.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
    font = None
    for path in font_paths:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, 54)
                break
            except: continue
    if not font: font = ImageFont.load_default()

    lines = textwrap.wrap(text, width=18)
    line_h = 60
    y_start = image.height - (line_h * len(lines)) - 80
    
    overlay = Image.new('RGBA', image.size, (0,0,0,0))
    d = ImageDraw.Draw(overlay)
    d.rectangle([0, y_start - 20, image.width, image.height], fill=(0, 0, 0, 160))
    image.paste(Image.alpha_composite(image.convert('RGBA'), overlay).convert('RGB'))

    curr_y = y_start
    for line in lines:
        try:
            bbox = draw.textbbox((0, 0), line, font=font)
            w = bbox[2] - bbox[0]
        except: w = len(line) * 25
        x = (image.width - w) / 2
        draw.text((x, curr_y), line, font=font, fill="white", stroke_width=2, stroke_fill="black")
        curr_y += line_h
        
    out_path = f"pun_{random.randint(1000,9999)}.jpg"
    image.save(out_path, quality=90)
    return out_path

async def send_generated_photo(message: types.Message, data: bytes, filename: str):
    try:
        input_file = types.BufferedInputFile(data, filename=filename)
        await message.reply_photo(input_file)
    except Exception as e:
        logging.error(f"Ошибка отправки фото: {e}")
        await message.reply("Не удалось отправить картинку из-за ошибки Telegram.")

# =============================================================================
# ГЕНЕРАТОРЫ
# =============================================================================

async def pollinations_generate(prompt: str) -> Optional[bytes]:
    """Генерация через Pollinations (Flux) - ПРИОРИТЕТ 1"""
    model_choice = random.choice(['flux', 'flux-pro', 'any-dark'])
    url = f"https://image.pollinations.ai/prompt/{prompt}?width=1024&height=1024&nologo=true&model={model_choice}&seed={random.randint(1, 99999)}"
    try:
        r = await asyncio.to_thread(lambda: requests.get(url, timeout=30))
        return r.content if r.status_code == 200 else None
    except: return None

async def hf_generate(prompt: str, model_id: str) -> Optional[bytes]:
    if not HF_TOKEN: return None
    url = f"https://api-inference.huggingface.co/models/{model_id}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {"inputs": prompt, "parameters": {"negative_prompt": "blurry, low quality, distorted"}}
    try:
        r = await asyncio.to_thread(lambda: requests.post(url, headers=headers, json=payload, timeout=60))
        if r.status_code == 200: return r.content
        return None
    except: return None

async def cf_generate_t2i(prompt: str) -> Optional[bytes]:
    if not CF_ACCOUNT_ID or not CF_API_TOKEN: return None
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/stabilityai/stable-diffusion-xl-base-1.0"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    try:
        r = await asyncio.to_thread(lambda: requests.post(url, headers=headers, json={"prompt": prompt, "num_steps": 25}, timeout=60))
        return r.content if r.status_code == 200 else None
    except: return None

# =============================================================================
# ГЛАВНЫЙ ОРКЕСТРАТОР (WATERFALL)
# =============================================================================

async def robust_image_generation(message: types.Message, prompt_ru: str, processing_msg: types.Message):
    global PIPELINE_ID
    
    # 1. ПРИОРИТЕТ: Pollinations.ai (Flux) - Самый стабильный и качественный
    await processing_msg.edit_text("🎨 Рисую через Flux (High Quality)...")
    prompt_en = await translate_to_en(prompt_ru)
    img = await pollinations_generate(prompt_en)
    if img:
        logging.info(f"[SUCCESS] Pollinations Flux | User: {message.from_user.id}")
        await processing_msg.delete()
        await send_generated_photo(message, img, "flux.png")
        return

    # 2. ПРИОРИТЕТ: Kandinsky (FusionBrain) - Если Flux подвел
    await processing_msg.edit_text("🔄 Flux занят, пробую Kandinsky...")
    if not PIPELINE_ID: 
        PIPELINE_ID = await asyncio.to_thread(kandinsky_api.get_pipeline)
    
    if PIPELINE_ID:
        uuid, err = await asyncio.to_thread(kandinsky_api.generate, prompt_ru, PIPELINE_ID)
        if uuid:
            img, _ = await asyncio.to_thread(kandinsky_api.check, uuid)
            if img:
                logging.info(f"[SUCCESS] Kandinsky | User: {message.from_user.id}")
                await processing_msg.delete()
                await send_generated_photo(message, img, "kandinsky.png")
                return

    # 3. ПРИОРИТЕТ: Hugging Face
    hf_models = ['black-forest-labs/FLUX.1-schnell', 'stabilityai/stable-diffusion-xl-base-1.0']
    for m_id in hf_models:
        await processing_msg.edit_text(f"🚀 Запускаю резерв ({m_id.split('/')[-1]})...")
        img = await hf_generate(prompt_en, m_id)
        if img:
            await processing_msg.delete()
            await send_generated_photo(message, img, "hf_image.png")
            return

    # 4. ПРИОРИТЕТ: Cloudflare (Финальный бэкап)
    await processing_msg.edit_text("🔌 Использую аварийный канал...")
    img = await cf_generate_t2i(prompt_en)
    if img:
        await processing_msg.delete()
        await send_generated_photo(message, img, "cloudflare.png")
        return

    await processing_msg.edit_text("❌ Все художники ушли на перерыв. Попробуйте позже.")

# =============================================================================
# ПУБЛИЧНЫЕ ХЭНДЛЕРЫ
# =============================================================================

async def handle_image_generation_command(message: types.Message):
    """Команда 'нарисуй'"""
    prompt = message.text.lower().replace("нарисуй", "").strip()
    if not prompt and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    if not prompt: return await message.reply("Что нарисовать-то?")
    
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    msg = await message.reply("🎨 Начинаю творческий процесс...")
    await robust_image_generation(message, prompt, msg)

async def handle_kandinsky_generation_command(message: types.Message):
    """Команда 'сгенерируй' - теперь тоже использует общий робастный метод"""
    prompt = message.text.lower().replace("сгенерируй", "").strip()
    if not prompt and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    if not prompt: return await message.reply("Что сгенерировать?")
    
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    msg = await message.reply("🚀 Запускаю нейронную сеть...")
    # Используем общую логику, так как она надежнее
    await robust_image_generation(message, prompt, msg)

async def handle_pun_image_command(message: types.Message):
    """Каламбур с картинкой"""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    msg = await message.reply("Придумываю каламбур...")
    try:
        pun_prompt = "Придумай смешной визуальный каламбур на русском. Формат: слово1+слово2 = итоговоеслово."
        pun_res = await asyncio.to_thread(lambda: model.generate_content(pun_prompt).text.strip())
        pun_res = pun_res.replace('"', '').replace("'", "").strip()
        
        if '=' not in pun_res: return await msg.edit_text("Не смог придумать каламбур.")
        
        parts = pun_res.split('=')
        source, final_word = parts[0].strip(), parts[1].strip()
        prompt_en = f"Funny surreal hybrid art of {final_word}, {source}, high detail"
        
        img_data = await pollinations_generate(prompt_en)
        if not img_data: img_data = await cf_generate_t2i(prompt_en)
            
        if img_data:
            path = await asyncio.to_thread(_overlay_text_on_image, img_data, final_word)
            await message.reply_photo(types.FSInputFile(path))
            os.remove(path)
            await msg.delete()
        else:
            await msg.edit_text(f"Каламбур: {pun_res}\nНо нарисовать не вышло.")
    except Exception as e:
        await msg.edit_text(f"Ошибка каламбура: {e}")

async def handle_redraw_command(message: types.Message):
    """Перерисовка"""
    photo = message.photo[-1] if message.photo else (message.reply_to_message.photo[-1] if message.reply_to_message and message.reply_to_message.photo else None)
    if not photo: return await message.reply("Пришли картинку для перерисовки.")
    
    msg = await message.reply("🔍 Анализирую изображение...")
    try:
        img_bytes = await download_telegram_image(bot, photo)
        analysis_prompt = "Describe this image in detail for an AI image generator prompt. Style: detailed digital art."
        desc = await asyncio.to_thread(lambda: model.generate_content([analysis_prompt, {"mime_type": "image/jpeg", "data": img_bytes}]))
        await robust_image_generation(message, desc.text.strip(), msg)
    except Exception as e:
        await msg.edit_text("Не удалось проанализировать картинку.")

async def handle_edit_command(message: types.Message):
    """Редактирование через CF"""
    photo = message.photo[-1] if message.photo else (message.reply_to_message.photo[-1] if message.reply_to_message and message.reply_to_message.photo else None)
    if not photo: return await message.reply("Нужно фото для редактирования.")
    
    prompt = (message.caption or message.text or "").lower().replace("отредактируй", "").strip()
    if not prompt: return await message.reply("Напиши, что изменить.")
    
    msg = await message.reply("🛠 Редактирую...")
    try:
        img_bytes = await download_telegram_image(bot, photo)
        en_prompt = await translate_to_en(prompt)
        
        img = Image.open(BytesIO(img_bytes)).convert("RGB").resize((512, 512))
        buf = BytesIO()
        img.save(buf, format="PNG")
        
        url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/runwayml/stable-diffusion-v1-5-img2img"
        r = await asyncio.to_thread(lambda: requests.post(
            url, 
            headers={"Authorization": f"Bearer {CF_API_TOKEN}"}, 
            json={"prompt": en_prompt, "image": list(buf.getvalue()), "strength": 0.5},
            timeout=60
        ))
        
        if r.status_code == 200:
            await msg.delete()
            await send_generated_photo(message, r.content, "edited.png")
        else:
            await msg.edit_text("Сервис редактирования временно недоступен.")
    except Exception:
        await msg.edit_text("Произошла ошибка при обработке.")
