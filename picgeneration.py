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
            # Логируем, что мы нашли
            if data:
                logging.info(f"Kandinsky Pipelines found: {len(data)}. Using: {data[0].get('name')} (ID: {data[0].get('id')})")
            
            if data and 'id' in data[0]:
                return data[0]['id']
            else:
                logging.error("API не вернул ожидаемую структуру для pipeline.")
                return None
        except requests.RequestException as e:
            logging.error(f"Ошибка при получении pipeline: {e}")
            return None

    def generate(self, prompt, pipeline, images=1, width=1024, height=1024):
        # Ограничиваем длину промпта
        if len(prompt) > 900:
            prompt = prompt[:900]
            logging.warning(f"Промпт обрезан до 900 символов")
        
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
            logging.info(f"Kandinsky request params: {json.dumps(params, ensure_ascii=False)[:200]}")
            response = requests.post(self.URL + 'key/api/v1/pipeline/run', headers=self.AUTH_HEADERS, files=data)
            
            # Разрешаем 201 (Created) и 200 (OK)
            if response.status_code not in [200, 201]:
                logging.error(f"Kandinsky API error {response.status_code}: {response.text}")
            
            response.raise_for_status()
            data = response.json()
            
            if 'uuid' in data:
                return data['uuid'], None
            
            error_message = data.get('errorDescription') or data.get('message') or data.get('pipeline_status') or json.dumps(data)
            logging.error(f"Kandinsky API не вернул UUID. Ответ: {error_message}")
            return None, error_message
            
        except requests.RequestException as e:
            logging.error(f"HTTP ошибка при запуске генерации: {e}")
            if hasattr(e.response, 'text'):
                logging.error(f"Response body: {e.response.text}")
            return None, str(e)
        except json.JSONDecodeError as e:
            logging.error(f"Ошибка декодирования JSON ответа: {response.text}")
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
                        logging.warning(f"Генерация {request_id} была зацензурена.")
                        return None, "Изображение было зацензурено (NSFW фильтр)."
                    return data.get('result', {}).get('files'), None
                
                elif status == 'FAIL':
                    # ЛОГИРУЕМ ПОЛНЫЙ ОТВЕТ, чтобы понять причину Unknown Error
                    logging.error(f"Kandinsky FAIL Full Response: {json.dumps(data, ensure_ascii=False)}")
                    
                    error_desc = data.get('errorDescription')
                    if not error_desc:
                        error_desc = "Неизвестная ошибка (см. логи)"
                    
                    return None, error_desc
                
                # Если INITIAL или PROCESSING, ждем
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
        # Пробуем получить еще раз, если при старте не вышло
        retry_pipeline = api.get_pipeline()
        if not retry_pipeline:
            return False, "Не удалось получить ID модели Kandinsky.", None
    else:
        retry_pipeline = pipeline_id

    try:
        loop = asyncio.get_event_loop()
        uuid, error = await loop.run_in_executor(None, api.generate, prompt, retry_pipeline)
        
        if error:
            return False, f"Не удалось запустить генерацию: {error}", None
            
        files, check_error = await loop.run_in_executor(None, api.check_generation, uuid)
        
        if check_error:
            return False, f"Ошибка при генерации: {check_error}", None
            
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
    """Переводит текст на английский"""
    if not text: 
        return ""
    try:
        translation_prompt = f"Translate the following text to English for an image generation prompt. Output only the translation, no explanations: {text}"
        response = await asyncio.to_thread(lambda: model.generate_content(translation_prompt).text)
        translated = response.strip()
        logging.info(f"Перевод: '{text}' -> '{translated}'")
        return translated
    except Exception as e:
        logging.error(f"Ошибка перевода: {e}")
        return text

# =============================================================================
# Функции для работы с Cloudflare Workers AI
# =============================================================================

async def save_and_send_generated_image(message: types.Message, image_data: bytes, filename="image.png"):
    """Отправляет изображение в чат с валидацией"""
    try:
        if not image_data:
            raise ValueError("Пустые данные изображения")

        try:
            with Image.open(BytesIO(image_data)) as img:
                img.verify()
        except Exception as e:
            if len(image_data) < 1000:
                try:
                    text_content = image_data.decode('utf-8', errors='ignore')
                    logging.error(f"Пришли невалидные данные (возможно текст ошибки): {text_content}")
                except:
                    pass
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

async def generate_image_with_cloudflare(prompt: str, source_image_bytes: bytes = None):
    """
    Генерация через Cloudflare (Stability AI SDXL Base).
    """
    if not CF_ACCOUNT_ID or not CF_API_TOKEN or CF_ACCOUNT_ID == "NO_CF_ID":
        return 'ERROR', {'error': "Cloudflare Credentials not found."}

    # Используем стабильную модель SDXL Base
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/stabilityai/stable-diffusion-xl-base-1.0"
    
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}"
    }
    
    payload = {
        "prompt": prompt,
        "num_steps": 20, 
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
            return 'ERROR', {'error': f"Ошибка обработки исходного: {e}"}

    def _sync_request():
        # Добавлен таймаут 60 секунд, чтобы не висело вечно
        return requests.post(url, headers=headers, json=payload, timeout=60)

    try:
        logging.info(f"Запрос к Cloudflare AI: {prompt[:50]}...")
        response = await asyncio.to_thread(_sync_request)
        
        if response.status_code == 200:
            return 'SUCCESS', {'image_data': response.content}
        else:
            logging.error(f"Cloudflare Error {response.status_code}: {response.text}")
            return 'ERROR', {'error': f"CF Error: {response.status_code}"}
            
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
# ОБЩАЯ ЛОГИКА ГЕНЕРАЦИИ (KANDINSKY PRIMARY, CF FALLBACK)
# =============================================================================

async def robust_image_generation(message: types.Message, prompt: str, processing_msg: types.Message, mode="text2img", source_bytes=None, is_pun=False):
    """
    Логика: Kandinsky -> если ошибка -> Cloudflare
    """
    # 1. Kandinsky
    success, error, k_data = await process_kandinsky_generation(prompt)
    
    if success:
        await processing_msg.delete()
        await save_and_send_generated_image(message, k_data, filename="kandinsky.png")
        return

    # 2. Cloudflare
    logging.warning(f"Kandinsky failed: {error}. Switching to Cloudflare.")
    
    if mode == "text2img":
        await processing_msg.edit_text("🎨 пися хуй")
        english_prompt = await translate_to_english(prompt)
        
        status, data = await generate_image_with_cloudflare(english_prompt)
        if status == 'SUCCESS':
            await processing_msg.delete()
            await save_and_send_generated_image(message, data['image_data'], filename="cloudflare_backup.png")
        else:
            await processing_msg.edit_text(f"Оба художника пьяны.\nKandinsky Error: {error}\nCF Error: {data.get('error')}")
    else:
        await processing_msg.edit_text(f"Не удалось обработать изображение.\nОшибка: {error}")

# =============================================================================
# ХЭНДЛЕРЫ
# =============================================================================

async def handle_pun_image_command(message: types.Message):
    """Каламбур"""
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
        
        image_gen_prompt = f"Визуализация каламбура '{final_word}'. Сюрреалистичная картина, объединяющая концепции '{source_words}'. Без букв и текста на изображении. Фотореалистичный стиль. Высокое качество, детализация."
        
        success, err, k_data = await process_kandinsky_generation(image_gen_prompt)
        
        if success:
            try:
                modified_path = await asyncio.to_thread(_overlay_text_on_image, k_data, final_word)
                await message.reply_photo(FSInputFile(modified_path))
                os.remove(modified_path)
                await processing_msg.delete()
            except Exception as e:
                logging.error(f"Ошибка наложения текста: {e}")
                await processing_msg.edit_text(f"Картинка есть, но текст наложить не вышло: {e}")
                await save_and_send_generated_image(message, k_data)
        else:
            # Fallback
            await processing_msg.edit_text("Кандинский не ответил, пробую Cloudflare с английским каламбуром...")
            
            english_pun_prompt = """Create a pun by combining two words into one. There should be an overlap between the end of the first word and the beginning of the second.
At least two letters should match.
Do not comment on the generation.
Answer strictly in the format: "word1+word2 = finalword"
For example: "butter+butterfly = butterflutter" """
            
            def sync_call_en():
                return model.generate_content(english_pun_prompt).text.strip()
            pun_word_en = await asyncio.to_thread(sync_call_en)
            
            parts_en = pun_word_en.split('=')
            if len(parts_en) != 2:
                await processing_msg.edit_text(f"Cloudflare fallback failed: invalid pun format. Response: {pun_word_en}")
                return
            
            source_words_en = parts_en[0].strip()
            final_word_en = parts_en[1].strip()
            
            image_gen_prompt_en = f"Visualization of pun '{final_word_en}'. Surrealistic painting combining concepts '{source_words_en}'. No letters or text on the image. Photorealistic style. High quality, detailed."
            
            status, data = await generate_image_with_cloudflare(image_gen_prompt_en)
            
            if status == 'SUCCESS':
                try:
                    modified_path = await asyncio.to_thread(_overlay_text_on_image, data['image_data'], final_word_en)
                    await message.reply_photo(FSInputFile(modified_path))
                    os.remove(modified_path)
                    await processing_msg.delete()
                except Exception as e:
                    logging.error(f"Ошибка наложения текста (CF): {e}")
                    await processing_msg.edit_text(f"Картинка есть, но текст наложить не вышло: {e}")
                    await save_and_send_generated_image(message, data['image_data'])
            else:
                await processing_msg.edit_text(f"Ошибка генерации картинки: {data.get('error')}")

    except Exception as e:
        logging.error(f"Ошибка в handle_pun_image_command: {e}", exc_info=True)
        await processing_msg.edit_text(f"Ошибка: {str(e)}")

async def handle_image_generation_command(message: types.Message):
    """Нарисуй"""
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
    """Перерисуй"""
    msg = await message.reply("Анал лизирую твою мазню")
    try:
        photo = message.photo[-1] if message.photo else (message.document if message.document else None)
        if not photo and message.reply_to_message:
            photo = message.reply_to_message.photo[-1] if message.reply_to_message.photo else message.reply_to_message.document
        
        if not photo:
            await msg.edit_text("Нет картинки.")
            return

        img_bytes = await download_telegram_image(bot, photo)
        
        prompt_desc = "Опиши эту картинку детально на русском языке. Сосредоточься на визуальных элементах, объектах, цветах. Описание будет использовано для воссоздания изображения в стиле 'плохой детский рисунок карандашами'."
        resp = await asyncio.to_thread(lambda: model.generate_content([prompt_desc, {"mime_type": "image/jpeg", "data": img_bytes}]))
        russian_desc = resp.text.strip()
        
        full_prompt = f"Детский рисунок карандашами, плохой стиль, каракули. {russian_desc}"
        await robust_image_generation(message, full_prompt, msg, mode="text2img")
        
    except Exception as e:
        logging.error(f"Redraw error: {e}")
        await msg.edit_text("Ошибка перерисовки.")

async def generate_img2img_cloudflare(prompt: str, source_image_bytes: bytes):
    """Img2Img Cloudflare"""
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        return 'ERROR', "Cloudflare Credentials not found."

    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/runwayml/stable-diffusion-v1-5-img2img"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}

    try:
        img = Image.open(BytesIO(source_image_bytes)).convert("RGB")
        img = img.resize((512, 512))
        img_buf = BytesIO()
        img.save(img_buf, format="PNG")
        img_bytes_final = img_buf.getvalue()

        payload = {
            "prompt": prompt,
            "image": list(img_bytes_final), 
            "num_steps": 20,
            "strength": 0.7, 
            "guidance": 7.5
        }

        def _sync_request():
            return requests.post(url, headers=headers, json=payload, timeout=60)

        response = await asyncio.to_thread(_sync_request)
        
        if response.status_code == 200:
            return 'SUCCESS', response.content
        else:
            try:
                err_text = response.json()
            except:
                err_text = response.text
            logging.error(f"CF Img2Img Error {response.status_code}: {err_text}")
            return 'ERROR', f"CF Error: {response.status_code}"

    except Exception as e:
        logging.error(f"Ошибка в generate_img2img_cloudflare: {e}", exc_info=True)
        return 'ERROR', str(e)

async def handle_edit_command(message: types.Message):
    """Отредактируй"""
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
    """Сгенерируй"""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompt = message.text.replace("сгенерируй", "").strip()
    msg = await message.reply("Гондинский работает...")
    success, err, data = await process_kandinsky_generation(prompt)
    if success:
        await msg.delete()
        await save_and_send_generated_image(message, data, "kandinsky.png")
    else:
        await msg.edit_text(f"Ошибка: {err}")
