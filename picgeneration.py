import requests
import json
import time
import asyncio
import os
import logging
import random
import textwrap
import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from aiogram import types
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramBadRequest
import google.generativeai as genai 
from google.api_core.exceptions import ResourceExhausted

# Импортируем настройки. 
import config
from config import (
    KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY, 
    bot, model, edit_model, API_TOKEN
)
from prompts import actions
from adddescribe import download_telegram_image

# Безопасное получение ключей
CF_ACCOUNT_ID = getattr(config, 'CLOUDFLARE_ACCOUNT_ID', None)
CF_API_TOKEN = getattr(config, 'CLOUDFLARE_API_TOKEN', None)
HF_TOKEN = getattr(config, 'HUGGINGFACE_TOKEN', None)

# =============================================================================
# Класс и функции для работы с API Kandinsky (FusionBrain)
# =============================================================================

class FusionBrainAPI:
    def __init__(self, url, api_key, secret_key):
        self.URL = url
        self.AUTH_HEADERS = {
            'X-Key': f'Key {api_key}',
            'X-Secret': f'Secret {secret_key}',
        }

    def get_pipeline(self):
        try:
            response = requests.get(self.URL + 'key/api/v1/pipelines', headers=self.AUTH_HEADERS)
            response.raise_for_status()
            data = response.json()
            if data:
                # logging.info(f"Kandinsky Pipelines found: {len(data)}. Using: {data[0].get('name')}")
                if 'id' in data[0]:
                    return data[0]['id']
            logging.error("API не вернул ожидаемую структуру для pipeline.")
            return None
        except requests.RequestException as e:
            logging.error(f"Ошибка при получении pipeline: {e}")
            return None

    def generate(self, prompt, pipeline, images=1, width=1024, height=1024):
        if len(prompt) > 900:
            prompt = prompt[:900]
        
        params = {
            "type": "GENERATE",
            "numImages": images,
            "width": width,
            "height": height,
            "generateParams": {
                "query": prompt
            }
        }
        
        data = {
            'pipeline_id': (None, pipeline),
            'params': (None, json.dumps(params), 'application/json')
        }
        
        try:
            response = requests.post(self.URL + 'key/api/v1/pipeline/run', headers=self.AUTH_HEADERS, files=data)
            response.raise_for_status()
            data = response.json()
            
            if 'uuid' in data:
                return data['uuid'], None
            
            error_message = data.get('errorDescription') or data.get('message') or json.dumps(data)
            return None, error_message
            
        except requests.RequestException as e:
            return None, str(e)
        except json.JSONDecodeError:
            return None, "API вернул некорректный JSON."

    def check_generation(self, request_id, attempts=15, delay=5):
        """Проверка статуса с расширенным логированием ошибок"""
        while attempts > 0:
            try:
                response = requests.get(self.URL + 'key/api/v1/pipeline/status/' + request_id, headers=self.AUTH_HEADERS)
                response.raise_for_status()
                data = response.json()
                
                status = data.get('status')
                
                if status == 'DONE':
                    if data.get('result', {}).get('censored', False):
                        return None, "Изображение было зацензурено (NSFW фильтр)."
                    return data.get('result', {}).get('files'), None
                
                elif status == 'FAIL':
                    error_desc = data.get('errorDescription') or "Неизвестная ошибка"
                    return None, error_desc
                
                attempts -= 1
                time.sleep(delay)
                
            except requests.RequestException as e:
                return None, str(e)
            except json.JSONDecodeError:
                attempts -= 1
                time.sleep(delay)
                
        return None, "Превышено время ожидания ответа от API."

api = FusionBrainAPI('https://api-key.fusionbrain.ai/', KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY)
pipeline_id = api.get_pipeline()

async def process_kandinsky_generation(prompt):
    global pipeline_id
    if not pipeline_id:
        pipeline_id = api.get_pipeline()
        if not pipeline_id:
            return False, "Не удалось получить ID модели Kandinsky.", None

    try:
        loop = asyncio.get_event_loop()
        uuid, error = await loop.run_in_executor(None, api.generate, prompt, pipeline_id)
        
        if error:
            return False, f"Не удалось запустить генерацию: {error}", None
            
        files, check_error = await loop.run_in_executor(None, api.check_generation, uuid)
        
        if check_error:
            return False, f"Ошибка при генерации: {check_error}", None
            
        if not files:
            return False, "Kandinsky: не вернул файлы", None
            
        image_data_base64 = files[0]
        try:
            base64_data = image_data_base64.split(',')[1] if ',' in image_data_base64 else image_data_base64
            image_data = base64.b64decode(base64_data)
            return True, None, image_data
        except Exception as e:
            return False, f"Ошибка декодирования: {str(e)}", None
    except Exception as e:
        import traceback
        logging.error(f"Критическая ошибка в process_kandinsky_generation: {traceback.format_exc()}")
        return False, f"Критическая ошибка: {repr(e)[:300]}", None

async def translate_to_english(text):
    if not text: 
        return ""
    try:
        translation_prompt = f"Translate the following text to English for an image generation prompt. Output only the translation, no explanations: {text}"
        response = await asyncio.to_thread(lambda: model.generate_content(translation_prompt).text)
        translated = response.strip()
        return translated
    except Exception as e:
        logging.error(f"Ошибка перевода: {e}")
        return text

async def save_and_send_generated_image(message: types.Message, image_data: bytes, filename="image.png"):
    try:
        if not image_data:
            raise ValueError("Пустые данные изображения")
        try:
            with Image.open(BytesIO(image_data)) as img:
                img.verify()
        except Exception as e:
            logging.error(f"FATAL: Полученные данные не являются изображением: {e}")
            await message.reply("Сервер генерации вернул ошибку вместо картинки.")
            return

        input_file = types.BufferedInputFile(image_data, filename=filename)
        await message.reply_photo(input_file)
    except TelegramBadRequest as e:
        logging.error(f"TelegramBadRequest (IMAGE_PROCESS_FAILED): {e}")
        await message.reply("Telegram не смог обработать этот файл.")
    except Exception as e:
        logging.error(f"Ошибка отправки изображения: {e}")
        await message.reply("Ошибка при отправке файла.")

# =============================================================================
# HUGGING FACE INFERENCE API (PRIORITY 1)
# =============================================================================

async def generate_image_huggingface(prompt: str):
    if not HF_TOKEN:
        return False, "HUGGINGFACE_TOKEN не задан", None

    # Используем стабильную модель SDXL или FLUX, если доступна
    API_URL = "https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-xl-base-1.0"

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "image/png"
    }

    payload = {
        "inputs": prompt,
        "options": {
            "wait_for_model": True,
            "use_cache": False
        }
    }

    def _sync():
        return requests.post(API_URL, headers=headers, json=payload, timeout=120)

    try:
        response = await asyncio.to_thread(_sync)

        if response.status_code == 200:
            return True, None, response.content

        if response.status_code == 503:
            return False, "Модель прогревается (503).", None

        return False, f"HF Error {response.status_code}: {response.text[:200]}", None

    except Exception as e:
        return False, f"HF Exception: {e}", None

# =============================================================================
# CLOUDFLARE (PRIORITY 3 - FALLBACK)
# =============================================================================
async def generate_image_with_cloudflare(prompt: str, source_image_bytes: bytes = None):
    if not CF_ACCOUNT_ID or not CF_API_TOKEN or CF_ACCOUNT_ID == "NO_CF_ID":
        return 'ERROR', {'error': "Cloudflare Credentials not found."}

    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/stabilityai/stable-diffusion-xl-base-1.0"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    payload = {"prompt": prompt, "num_steps": 20, "guidance": 7.5, "width": 1024, "height": 1024}

    if source_image_bytes:
        try:
            image_b64 = base64.b64encode(source_image_bytes).decode('utf-8')
            payload["image_b64"] = image_b64
            payload["strength"] = 0.6 
        except Exception as e:
            return 'ERROR', {'error': f"Ошибка обработки исходного: {e}"}

    def _sync_request():
        return requests.post(url, headers=headers, json=payload, timeout=60)

    try:
        response = await asyncio.to_thread(_sync_request)
        if response.status_code == 200:
            return 'SUCCESS', {'image_data': response.content}
        else:
            return 'ERROR', {'error': f"CF Error: {response.status_code}"}
    except Exception as e:
        return 'ERROR', {'error': str(e)}


def _overlay_text_on_image(image_bytes: bytes, text: str) -> str:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not os.path.exists(font_path): font_path = "arial.ttf"
    try: font = ImageFont.truetype(font_path, 48)
    except IOError: font = ImageFont.load_default()

    max_chars = 20
    lines = textwrap.wrap(text, width=max_chars)
    text_block_height = (50 + 5) * len(lines)
    y = image.height - text_block_height - 60
    
    rectangle = Image.new('RGBA', (image.width, text_block_height + 40), (0, 0, 0, 128))
    image.paste(rectangle, (0, y - 20), rectangle)
    
    current_y = y - 10
    for line in lines:
        try: text_w = font.getbbox(line)[2]
        except: text_w = len(line) * 10
        x = (image.width - text_w) / 2
        draw.text((x, current_y), line, font=font, fill="white", stroke_width=1, stroke_fill="black")
        current_y += 55
    
    output_path = f"pun_{random.randint(1000,9999)}.jpg"
    image.save(output_path)
    return output_path

async def robust_image_generation(message: types.Message, prompt: str, processing_msg: types.Message, mode="text2img", source_bytes=None, is_pun=False):
    """
    Основная функция-оркестратор генерации.
    Приоритет:
    1. Hugging Face (SDXL/Flux) - требует перевода на английский.
    2. Kandinsky - работает с русским.
    3. Cloudflare - требует перевода на английский.
    """
    
    # 1. Попытка Hugging Face (Priority 1)
    try:
        english_prompt = await translate_to_english(prompt)
        success_hf, error_hf, hf_data = await generate_image_huggingface(english_prompt)
        
        if success_hf:
            await processing_msg.delete()
            await save_and_send_generated_image(message, hf_data, filename="sdxl_hf.png")
            return
        else:
            logging.warning(f"HF Generation failed: {error_hf}. Switching to Kandinsky.")
            # Если не вышло, не удаляем сообщение, продолжаем.
    except Exception as e:
        logging.error(f"HF Critical Error: {e}")

    # 2. Попытка Kandinsky (Priority 2)
    await processing_msg.edit_text("Художник курит, зову Кандинского...")
    success_k, error_k, k_data = await process_kandinsky_generation(prompt)
    
    if success_k:
        await processing_msg.delete()
        await save_and_send_generated_image(message, k_data, filename="kandinsky.png")
        return

    logging.warning(f"Kandinsky failed: {error_k}. Switching to Cloudflare.")

    # 3. Попытка Cloudflare (Priority 3)
    if mode == "text2img":
        await processing_msg.edit_text("Кандинский запил, бужу Клаудфлеер...")
        # Промпт уже переведен для HF, используем его
        status, data = await generate_image_with_cloudflare(english_prompt)
        if status == 'SUCCESS':
            await processing_msg.delete()
            await save_and_send_generated_image(message, data['image_data'], filename="cloudflare_backup.png")
        else:
            await processing_msg.edit_text(f"Все художники в запое.\nHF Error: {error_hf}\nKandinsky Error: {error_k}\nCF Error: {data.get('error')}")
    else:
        await processing_msg.edit_text(f"Не удалось обработать изображение.\nОшибка: {error_k}")


# =============================================================================
# ХЭНДЛЕРЫ
# =============================================================================

async def handle_pun_image_command(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Генерирую хуйню...")
    
    pun_prompt = """составь каламбурное сочетание слов в одном слове. должно быть пересечение конца первого слова с началом второго. 
    Совпадать должны как минимум две буквы. 
    Не комментируй генерацию.
    Ответ дай строго в формате: "слово1+слово2 = итоговоеслово"
    Например: "манго+голубь = манголубь" """
    
    try:
        def sync_call():
            return model.generate_content(pun_prompt).text.strip()
        pun_word = await asyncio.to_thread(sync_call)
        parts = pun_word.split('=')
        if len(parts) != 2:
            await processing_msg.edit_text(f"Не удалось распознать каламбур. Ответ: {pun_word}")
            return
        
        source_words = parts[0].strip()
        final_word = parts[1].strip()
        
        # Промпт для генерации
        image_gen_prompt_ru = f"Визуализация каламбура '{final_word}'. Сюрреалистичная картина, объединяющая концепции '{source_words}'. Без букв и текста на изображении. Фотореалистичный стиль. Высокое качество, детализация."
        
        # 1. Пробуем HF (нужен перевод)
        english_desc = await translate_to_english(f"Surrealistic painting combining concepts {source_words}. No text.")
        success, err, img_data = await generate_image_huggingface(english_desc)
        
        if not success:
            # 2. Пробуем Kandinsky (русский)
            success, err, img_data = await process_kandinsky_generation(image_gen_prompt_ru)
            
        if not success:
             # 3. Пробуем CF (английский)
            status, data = await generate_image_with_cloudflare(english_desc)
            if status == 'SUCCESS':
                success = True
                img_data = data['image_data']
            else:
                err = data.get('error')

        # Если хоть кто-то сгенерировал
        if success and img_data:
            try:
                modified_path = await asyncio.to_thread(_overlay_text_on_image, img_data, final_word)
                await message.reply_photo(FSInputFile(modified_path))
                os.remove(modified_path)
                await processing_msg.delete()
            except Exception as e:
                await processing_msg.edit_text(f"Картинка есть, но текст наложить не вышло: {e}")
                await save_and_send_generated_image(message, img_data)
        else:
             await processing_msg.edit_text(f"Ошибка генерации картинки (все модели): {err}")

    except Exception as e:
        await processing_msg.edit_text(f"Ошибка: {str(e)}")

async def handle_image_generation_command(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompt = message.text.replace("нарисуй", "").strip()
    if not prompt and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    if not prompt:
        await message.reply("Что рисовать?")
        return
    msg = await message.reply("Ща падажжи ебана")
    full_prompt = f"{prompt}, высокое качество, шедевр, 8k"
    await robust_image_generation(message, full_prompt, msg, mode="text2img")

async def handle_redraw_command(message: types.Message):
    msg = await message.reply("Анал лизирую твою мазню")
    try:
        photo = message.photo[-1] if message.photo else (message.document if message.document else None)
        if not photo and message.reply_to_message:
            photo = message.reply_to_message.photo[-1] if message.reply_to_message.photo else message.reply_to_message.document
        if not photo:
            await msg.edit_text("Нет картинки.")
            return
        img_bytes = await download_telegram_image(bot, photo)
        prompt_desc = "Опиши эту картинку детально на русском языке. Сосредоточься на визуальных элементах."
        resp = await asyncio.to_thread(lambda: model.generate_content([prompt_desc, {"mime_type": "image/jpeg", "data": img_bytes}]))
        full_prompt = f"Детский рисунок карандашами, плохой стиль, каракули. {resp.text.strip()}"
        
        # Перерисовка через robust использует Text2Img с описанием.
        # Если нужен именно Img2Img, то HF (бесплатный API) обычно поддерживает только Text2Img. 
        # Поэтому логика robust_image_generation подходит (генерирует новую по описанию старой).
        await robust_image_generation(message, full_prompt, msg, mode="text2img")
    except Exception as e:
        await msg.edit_text("Ошибка перерисовки.")

async def generate_img2img_cloudflare(prompt: str, source_image_bytes: bytes):
    if not CF_ACCOUNT_ID or not CF_API_TOKEN: return 'ERROR', "Cloudflare Credentials not found."
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/runwayml/stable-diffusion-v1-5-img2img"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    try:
        img = Image.open(BytesIO(source_image_bytes)).convert("RGB")
        img = img.resize((512, 512))
        img_buf = BytesIO(); img.save(img_buf, format="PNG"); img_bytes_final = img_buf.getvalue()
        payload = {"prompt": prompt, "image": list(img_bytes_final), "num_steps": 20, "strength": 0.7, "guidance": 7.5}
        def _sync(): return requests.post(url, headers=headers, json=payload, timeout=60)
        response = await asyncio.to_thread(_sync)
        if response.status_code == 200: return 'SUCCESS', response.content
        else: return 'ERROR', f"CF Error: {response.status_code}"
    except Exception as e: return 'ERROR', str(e)

async def handle_edit_command(message: types.Message):
    msg = await message.reply("Ща блядь отредактирую")
    try:
        photo = message.photo[-1] if message.photo else None 
        if not photo and message.reply_to_message and message.reply_to_message.photo:
            photo = message.reply_to_message.photo[-1]
        if not photo:
            await msg.edit_text("Нужно фото для редактирования.")
            return
        prompt_text = message.caption or message.text
        prompt_text = prompt_text.lower().replace("/отредактируй", "").replace("отредактируй", "").strip()
        if not prompt_text:
            await msg.edit_text("Напишите, во что превратить фото.")
            return
        img_bytes = await download_telegram_image(bot, photo)
        english_prompt = await translate_to_english(prompt_text)
        
        # Для редактирования (Img2Img) пока оставляем Cloudflare, т.к. бесплатный HF Inference часто только Text2Img
        status, result = await generate_img2img_cloudflare(english_prompt, img_bytes)
        
        if status == 'SUCCESS':
            await msg.delete()
            await save_and_send_generated_image(message, result, filename="edited_img2img.png")
        else:
            await msg.edit_text(f"Не удалось отредактировать: {result}")
    except Exception as e:
        logging.error(f"Edit error: {e}", exc_info=True)
        await msg.edit_text("Произошла ошибка при обработке.")

async def handle_kandinsky_generation_command(message: types.Message):
    # Принудительная генерация через Кандинского (старая команда "сгенерируй")
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompt = message.text.replace("сгенерируй", "").strip()
    msg = await message.reply("Гондинский работает...")
    success, err, data = await process_kandinsky_generation(prompt)
    if success:
        await msg.delete()
        await save_and_send_generated_image(message, data, "kandinsky.png")
    else:
        await msg.edit_text(f"Ошибка: {err}")
