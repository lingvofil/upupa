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
from PIL import Image, ImageDraw, ImageFont
from aiogram import types
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramBadRequest

# Импортируем настройки. 
import config
from config import (
    KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY, 
    bot, model, edit_model, API_TOKEN
)
from prompts import actions
from adddescribe import download_telegram_image

# Безопасное получение ключей CF
CF_ACCOUNT_ID = getattr(config, 'CLOUDFLARE_ACCOUNT_ID', None)
CF_API_TOKEN = getattr(config, 'CLOUDFLARE_API_TOKEN', None)

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
            if data and 'id' in data[0]:
                return data[0]['id']
            else:
                logging.error("API не вернул ожидаемую структуру для pipeline.")
                return None
        except requests.RequestException as e:
            logging.error(f"Ошибка при получении pipeline: {e}")
            return None

    def generate(self, prompt, pipeline, images=1, width=1024, height=1024):
        params = {
            "type": "GENERATE",
            "numImages": images,
            "width": width,
            "height": height,
            "generateParams": {
                "query": f'{prompt}'
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
            error_message = data.get('errorDescription') or data.get('message') or data.get('pipeline_status') or json.dumps(data)
            logging.error(f"Kandinsky API не вернул UUID. Ответ: {error_message}")
            return None, error_message
        except requests.RequestException as e:
            logging.error(f"HTTP ошибка при запуске генерации: {e}")
            return None, str(e)
        except json.JSONDecodeError:
            logging.error(f"Ошибка декодирования JSON ответа: {response.text}")
            return None, "API вернул некорректный JSON."

    def check_generation(self, request_id, attempts=10, delay=10):
        while attempts > 0:
            try:
                response = requests.get(self.URL + 'key/api/v1/pipeline/status/' + request_id, headers=self.AUTH_HEADERS)
                response.raise_for_status()
                data = response.json()
                if data.get('status') == 'DONE':
                    if data.get('result', {}).get('censored', False):
                        logging.warning(f"Генерация {request_id} была зацензурена.")
                        return None, "Изображение было зацензурено."
                    return data.get('result', {}).get('files'), None
                if data.get('status') == 'FAIL':
                    error_desc = data.get('errorDescription', 'Неизвестная ошибка выполнения.')
                    logging.error(f"Генерация {request_id} провалена: {error_desc}")
                    return None, error_desc
                attempts -= 1
                time.sleep(delay)
            except requests.RequestException as e:
                logging.error(f"HTTP ошибка при проверке статуса: {e}")
                return None, str(e)
            except json.JSONDecodeError:
                logging.error(f"Ошибка декодирования JSON при проверке статуса: {response.text}")
                attempts -= 1
                time.sleep(delay)
        return None, "Превышено время ожидания ответа от API."

api = FusionBrainAPI('https://api-key.fusionbrain.ai/', KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY)
pipeline_id = api.get_pipeline()

async def process_kandinsky_generation(prompt):
    """Генерация через Kandinsky API"""
    if not pipeline_id:
        return False, "Не удалось получить ID модели Kandinsky.", None
    try:
        loop = asyncio.get_event_loop()
        uuid, error = await loop.run_in_executor(None, api.generate, prompt, pipeline_id)
        if error:
            return False, f"Не удалось запустить генерацию Kandinsky: {error}", None
        files, check_error = await loop.run_in_executor(None, api.check_generation, uuid)
        if check_error:
            return False, f"Ошибка при генерации Kandinsky: {check_error}", None
        if not files:
            return False, "Kandinsky: не вернул файлы", None
        image_data_base64 = files[0]
        try:
            if ',' in image_data_base64:
                base64_data = image_data_base64.split(',')[1]
            else:
                base64_data = image_data_base64
            image_data = base64.b64decode(base64_data)
            return True, None, image_data
        except Exception as e:
            logging.error(f"Ошибка декодирования base64: {e}")
            return False, f"Ошибка декодирования: {str(e)}", None
    except Exception as e:
        import traceback
        logging.error(f"Критическая ошибка в process_kandinsky_generation: {traceback.format_exc()}")
        return False, f"Критическая ошибка: {repr(e)[:300]}", None

# =============================================================================
# Вспомогательные функции перевода
# =============================================================================

async def translate_to_english(text):
    """Переводит текст на английский, используя основную LLM модель"""
    if not text: 
        return ""
    # Если текст уже на английском (простая эвристика), можно не переводить, 
    # но для надежности прогоняем всё, кроме очень коротких ASCII строк.
    try:
        translation_prompt = f"Translate the following text to English for an image generation prompt. Output only the translation, no explanations: {text}"
        response = await asyncio.to_thread(lambda: model.generate_content(translation_prompt).text)
        translated = response.strip()
        logging.info(f"Перевод: '{text}' -> '{translated}'")
        return translated
    except Exception as e:
        logging.error(f"Ошибка перевода: {e}")
        return text # Возвращаем оригинал в случае ошибки

# =============================================================================
# Функции для работы с Cloudflare Workers AI (SDXL Lightning)
# =============================================================================

async def save_and_send_generated_image(message: types.Message, image_data: bytes, filename="image.png"):
    """Отправляет изображение в чат"""
    try:
        input_file = types.BufferedInputFile(image_data, filename=filename)
        await message.reply_photo(input_file)
    except Exception as e:
        logging.error(f"Ошибка отправки изображения: {e}")
        await message.reply("Не удалось отправить сгенерированное изображение.")

async def generate_image_with_cloudflare(prompt: str, source_image_bytes: bytes = None):
    """
    Генерация через Cloudflare.
    Возвращает ('SUCCESS', data) или ('ERROR', msg).
    """
    if not CF_ACCOUNT_ID or not CF_API_TOKEN or CF_ACCOUNT_ID == "NO_CF_ID":
        return 'ERROR', {'error': "Cloudflare Credentials not found or invalid in Config."}

    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/bytedance/stable-diffusion-xl-lightning"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}"
    }
    
    payload = {
        "prompt": prompt,
        "num_steps": 8, 
        "guidance": 7.5,
        "width": 1024,
        "height": 1024
    }

    if source_image_bytes:
        try:
            image_b64 = base64.b64encode(source_image_bytes).decode('utf-8')
            payload["image_b64"] = image_b64
            payload["strength"] = 0.6 
        except Exception as e:
            return 'ERROR', {'error': f"Ошибка обработки исходного изображения: {e}"}

    def _sync_request():
        return requests.post(url, headers=headers, json=payload)

    try:
        logging.info(f"Запрос к Cloudflare AI: {prompt[:50]}...")
        response = await asyncio.to_thread(_sync_request)
        
        if response.status_code == 200:
            return 'SUCCESS', {'image_data': response.content}
        else:
            logging.error(f"Cloudflare Error {response.status_code}: {response.text}")
            return 'ERROR', {'error': f"Cloudflare Error: {response.status_code}"}
            
    except Exception as e:
        logging.error(f"Ошибка в generate_image_with_cloudflare: {e}", exc_info=True)
        return 'ERROR', {'error': str(e)}

# =============================================================================
# Вспомогательные функции (текст, оверлей)
# =============================================================================

def _overlay_text_on_image(image_bytes: bytes, text: str) -> str:
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not os.path.exists(font_path):
        font_path = "arial.ttf"
    try:
        font = ImageFont.truetype(font_path, 48)
    except IOError:
        font = ImageFont.load_default()

    max_chars = 20
    lines = textwrap.wrap(text, width=max_chars)
    
    line_height = 50
    text_block_height = (line_height + 5) * len(lines)
    y = image.height - text_block_height - 60
    
    rectangle = Image.new('RGBA', (image.width, text_block_height + 40), (0, 0, 0, 128))
    image.paste(rectangle, (0, y - 20), rectangle)
    
    current_y = y - 10
    for line in lines:
        try:
            text_w = font.getbbox(line)[2] if hasattr(font, 'getbbox') else font.getsize(line)[0]
        except:
            text_w = len(line) * 10
        x = (image.width - text_w) / 2
        draw.text((x, current_y), line, font=font, fill="white", stroke_width=1, stroke_fill="black")
        current_y += line_height + 5
    
    output_path = f"pun_{random.randint(1000,9999)}.jpg"
    image.save(output_path)
    return output_path

# =============================================================================
# ОБЩАЯ ЛОГИКА ГЕНЕРАЦИИ (С ФОЛЛБЭКОМ)
# =============================================================================

async def robust_image_generation(message: types.Message, prompt: str, processing_msg: types.Message, mode="text2img", source_bytes=None):
    """
    Пытается сгенерировать через Cloudflare.
    При неудаче автоматически переключается на Kandinsky.
    """
    # 1. Попытка Cloudflare
    status, data = await generate_image_with_cloudflare(prompt, source_bytes)
    
    if status == 'SUCCESS':
        await processing_msg.delete()
        await save_and_send_generated_image(message, data['image_data'], filename="sdxl.png")
        return

    # 2. Фоллбэк на Kandinsky
    logging.warning(f"Cloudflare failed: {data.get('error')}. Switching to Kandinsky.")
    
    if mode == "text2img":
        await processing_msg.edit_text("⚡️ Молния не сверкнула, запускаю Кандинского... 🎨")
        # Кандинский хорошо понимает и английский и русский, отправляем тот промпт, что есть (английский)
        success, error, k_data = await process_kandinsky_generation(prompt)
        if success:
            await processing_msg.delete()
            await save_and_send_generated_image(message, k_data, filename="kandinsky_backup.png")
        else:
            await processing_msg.edit_text(f"Оба художника пьяны.\nCF Error: {data.get('error')}\nKandinsky Error: {error}")
    else:
        # Для Img2Img (редактирование)
        await processing_msg.edit_text(f"Не удалось обработать изображение.\nОшибка: {data.get('error')}")

# =============================================================================
# ХЭНДЛЕРЫ
# =============================================================================

async def handle_pun_image_command(message: types.Message):
    """Каламбур"""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Генерирую каламбур...")
    
    pun_prompt = "составь каламбурное сочетание слов в одном слове (формат: слово1+слово2 = итог)."
    try:
        def sync_call():
            return model.generate_content(pun_prompt).text.strip()
        pun_text = await asyncio.to_thread(sync_call)
        
        if "=" in pun_text:
            parts = pun_text.split('=')
            source = parts[0].strip()
            final = parts[1].strip()
        else:
            source = pun_text
            final = pun_text

        # Формируем описание на русском для перевода
        description_ru = f"Сюрреалистичный арт, визуализация буквального каламбура: {source}. Фотореализм, 8k."
        
        # Переводим промпт для Cloudflare
        english_prompt = await translate_to_english(description_ru)
        
        # Пробуем CF
        status, data = await generate_image_with_cloudflare(english_prompt)
        
        if status == 'SUCCESS':
            try:
                path = await asyncio.to_thread(_overlay_text_on_image, data['image_data'], final)
                await message.reply_photo(FSInputFile(path))
                os.remove(path)
                await processing_msg.delete()
            except:
                await save_and_send_generated_image(message, data['image_data'])
        else:
            # Фоллбэк
            await processing_msg.edit_text("CF не ответил, пробую Кандинского...")
            success, err, k_data = await process_kandinsky_generation(english_prompt)
            if success:
                try:
                    path = await asyncio.to_thread(_overlay_text_on_image, k_data, final)
                    await message.reply_photo(FSInputFile(path))
                    os.remove(path)
                    await processing_msg.delete()
                except:
                    await save_and_send_generated_image(message, k_data)
            else:
                await processing_msg.edit_text("Не вышло нарисовать каламбур.")

    except Exception as e:
        logging.error(f"Err pun: {e}")
        await processing_msg.edit_text("Ошибка логики каламбура.")

async def handle_image_generation_command(message: types.Message):
    """Нарисуй"""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompt = message.text.replace("нарисуй", "").strip()
    if not prompt and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    
    if not prompt:
        await message.reply("Что рисовать?")
        return

    msg = await message.reply("Рисую...")
    
    # Переводим входящий промпт
    english_prompt = await translate_to_english(prompt)
    full_prompt = f"{english_prompt}, high quality, masterpiece, 8k"
    
    await robust_image_generation(message, full_prompt, msg, mode="text2img")

async def handle_redraw_command(message: types.Message):
    """Перерисуй"""
    msg = await message.reply("Смотрю картинку...")
    try:
        photo = message.photo[-1] if message.photo else (message.document if message.document else None)
        if not photo and message.reply_to_message:
            photo = message.reply_to_message.photo[-1] if message.reply_to_message.photo else message.reply_to_message.document
        
        if not photo:
            await msg.edit_text("Нет картинки.")
            return

        img_bytes = await download_telegram_image(bot, photo)
        
        # Просим Gemini описать картинку сразу на АНГЛИЙСКОМ
        prompt_desc = "Describe this image in detail in English. Focus on visual elements, objects, colors. The description will be used to recreate this image as a 'bad children's crayon drawing'."
        
        resp = await asyncio.to_thread(lambda: model.generate_content([prompt_desc, {"mime_type": "image/jpeg", "data": img_bytes}]))
        english_desc = resp.text.strip()
        
        full_prompt = f"Children's crayon drawing, bad style, scribbles. {english_desc}"
        
        await robust_image_generation(message, full_prompt, msg, mode="text2img")
        
    except Exception as e:
        logging.error(f"Redraw error: {e}")
        await msg.edit_text("Ошибка перерисовки.")

async def handle_edit_command(message: types.Message):
    """Отредактируй (Img2Img)"""
    msg = await message.reply("Редактирую (CF)...")
    try:
        photo = message.photo[-1] if message.photo else None 
        if message.reply_to_message and message.reply_to_message.photo:
            photo = message.reply_to_message.photo[-1]
            
        if not photo:
            await msg.edit_text("Нужно фото.")
            return
            
        prompt = message.caption or message.text
        prompt = prompt.lower().replace("отредактируй", "").strip()
        
        if not prompt:
            await msg.edit_text("Напишите, что сделать (например: 'добавь шляпу').")
            return

        img_bytes = await download_telegram_image(bot, photo)
        
        # Переводим инструкцию по редактированию
        english_prompt = await translate_to_english(prompt)
        
        # Пробуем CF Img2Img
        status, data = await generate_image_with_cloudflare(english_prompt, img_bytes)
        if status == 'SUCCESS':
            await msg.delete()
            await save_and_send_generated_image(message, data['image_data'])
        else:
            await msg.edit_text(f"Cloudflare Img2Img Error: {data.get('error')}")
            
    except Exception as e:
        logging.error(f"Edit error: {e}")
        await msg.edit_text("Ошибка редактирования.")

async def handle_kandinsky_generation_command(message: types.Message):
    """Сгенерируй (Принудительно Кандинский) - БЕЗ ИЗМЕНЕНИЙ"""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompt = message.text.replace("сгенерируй", "").strip()
    msg = await message.reply("Кандинский работает...")
    success, err, data = await process_kandinsky_generation(prompt)
    if success:
        await msg.delete()
        await save_and_send_generated_image(message, data, "kandinsky.png")
    else:
        await msg.edit_text(f"Ошибка: {err}")
