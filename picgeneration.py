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

# Импортируем ключи CLOUDFLARE. Убедитесь, что добавили их в config.py
from config import (
    KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY, 
    CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN,
    bot, model, edit_model, API_TOKEN
)
from prompts import actions
from adddescribe import download_telegram_image

# =============================================================================
# Класс и функции для работы с API Kandinsky (FusionBrain)
# (БЕЗ ИЗМЕНЕНИЙ)
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
        return False, "Не удалось получить ID модели от API.", None
    try:
        loop = asyncio.get_event_loop()
        uuid, error = await loop.run_in_executor(None, api.generate, prompt, pipeline_id)
        if error:
            return False, f"Не удалось запустить генерацию: {error}", None
        files, check_error = await loop.run_in_executor(None, api.check_generation, uuid)
        if check_error:
            return False, f"Ошибка при генерации: {check_error}", None
        if not files:
            return False, "Не получилось сгенерировать изображение (API не вернул файлы)", None
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
    Генерация изображения через Cloudflare SDXL Lightning.
    Если передан source_image_bytes, работает в режиме Img2Img.
    """
    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/@cf/bytedance/stable-diffusion-xl-lightning"
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"
    }
    
    # SDXL Lightning хорошо работает на малом кол-ве шагов (4-8)
    payload = {
        "prompt": prompt,
        "num_steps": 8, 
        "guidance": 7.5,
        "width": 1024,
        "height": 1024
    }

    # Если есть исходное изображение (для перерисовки/редактирования)
    if source_image_bytes:
        try:
            # Конвертируем байты в base64 строку
            image_b64 = base64.b64encode(source_image_bytes).decode('utf-8')
            payload["image_b64"] = image_b64
            # Для Img2Img strength влияет на то, как сильно меняется картинка (0.3 - мало, 0.7 - сильно)
            payload["strength"] = 0.6 
        except Exception as e:
            logging.error(f"Ошибка кодирования source_image для CF: {e}")
            return 'ERROR', {'error': "Ошибка обработки исходного изображения"}

    def _sync_request():
        response = requests.post(url, headers=headers, json=payload)
        return response

    try:
        logging.info(f"Запрос к Cloudflare AI: {prompt[:50]}...")
        response = await asyncio.to_thread(_sync_request)
        
        if response.status_code == 200:
            # Cloudflare возвращает бинарные данные (image/png) напрямую
            return 'SUCCESS', {'image_data': response.content}
        else:
            logging.error(f"Cloudflare Error {response.status_code}: {response.text}")
            return 'ERROR', {'error': f"Cloudflare API Error: {response.status_code} - {response.text[:100]}"}
            
    except Exception as e:
        logging.error(f"Ошибка в generate_image_with_cloudflare: {e}", exc_info=True)
        return 'ERROR', {'error': str(e)}

# =============================================================================
# Вспомогательные функции для текста на изображении
# =============================================================================

def _get_text_size(font, text):
    try:
        bbox = font.getbbox(text)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        return width, height
    except AttributeError:
        return font.getsize(text)

def _overlay_text_on_image(image_bytes: bytes, text: str) -> str:
    """Накладывает текст на изображение и возвращает путь к файлу"""
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not os.path.exists(font_path):
        font_path = "arial.ttf"
    font_size = 48
    try:
        font = ImageFont.truetype(font_path, font_size)
    except IOError:
        font = ImageFont.load_default()

    max_width = image.width - 40
    sample_chars = "абвгдежзийклмнопрстуфхцчшщъыьэюя"
    try:
        avg_char_width = sum(_get_text_size(font, char)[0] for char in sample_chars) / len(sample_chars)
        max_chars_per_line = int(max_width // avg_char_width) if avg_char_width > 0 else 20
    except:
        max_chars_per_line = 20

    lines = textwrap.wrap(text, width=max_chars_per_line)
    try:
        _, line_height = _get_text_size(font, "A")
    except:
        line_height = 50

    text_block_height = (line_height + 5) * len(lines)
    margin_bottom = 60
    y = image.height - text_block_height - margin_bottom
    rectangle = Image.new('RGBA', (image.width, text_block_height + 40), (0, 0, 0, 128))
    image.paste(rectangle, (0, y - 20), rectangle)
    current_y = y - 10
    for line in lines:
        text_width, _ = _get_text_size(font, line)
        x = (image.width - text_width) / 2
        draw.text((x, current_y), line, font=font, fill="white", stroke_width=1, stroke_fill="black")
        current_y += line_height + 5
    
    output_path = f"modified_pun_image_{random.randint(1000,9999)}.jpg"
    image.save(output_path)
    return output_path

# =============================================================================
# ХЭНДЛЕРЫ КОМАНД
# =============================================================================

async def handle_pun_image_command(message: types.Message):
    """Каламбур - генерирует каламбурное слово (Gemini) и рисует его (Cloudflare)"""
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

        # Промпт для SDXL лучше делать на английском, но CF понимает и русский
        # Для надежности можно попросить Gemini перевести промпт, но пока попробуем так
        image_gen_prompt = f"Surreal painting, visualization of a pun '{final_word}', combining concepts of '{source_words}'. No text, no letters. Photorealistic style, 8k, high detailed."
        
        # ИСПОЛЬЗУЕМ CLOUDFLARE
        status, data = await generate_image_with_cloudflare(image_gen_prompt)

        if status == 'SUCCESS':
            image_data = data['image_data']
            try:
                modified_path = await asyncio.to_thread(_overlay_text_on_image, image_data, final_word)
                await message.reply_photo(FSInputFile(modified_path))
                os.remove(modified_path)
                await processing_msg.delete()
            except Exception as e:
                await processing_msg.edit_text(f"Картинка есть, но текст наложить не вышло: {e}")
                await save_and_send_generated_image(message, image_data, filename="pun.png")
        else:
            await processing_msg.edit_text(f"Ошибка генерации картинки через CF: {data.get('error')}")

    except Exception as e:
        logging.error(f"Ошибка в handle_pun_image_command: {e}", exc_info=True)
        await processing_msg.edit_text(f"Ошибка: {str(e)}")


async def handle_image_generation_command(message: types.Message):
    """Нарисуй - генерация через Cloudflare SDXL"""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompt = None
    if message.text.lower().strip() == "нарисуй" and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    elif message.text.lower().startswith("нарисуй "):
        prompt = message.text[len("нарисуй "):].strip()
    if not prompt:
        await message.reply("Шо именно нарисовать-то?")
        return
    processing_message = await message.reply("Ща падажжи, ебана")
    
    # ИСПОЛЬЗУЕМ CLOUDFLARE
    # Можно добавить "cinematic, high quality" к промпту для улучшения качества
    full_prompt = f"{prompt}, high quality, masterpiece, 8k"
    
    status, data = await generate_image_with_cloudflare(full_prompt)
    
    if status == 'SUCCESS':
        await processing_message.delete()
        await save_and_send_generated_image(message, data['image_data'], filename="sdxl_lightning.png")
    else:
        await processing_message.edit_text(f"Ошибка: {data.get('error')}")


async def handle_redraw_command(message: types.Message):
    """Перерисуй - анализирует изображение (Gemini) и перерисовывает (Cloudflare)"""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Анализирую тваю мазню...")
    try:
        photo = None
        if message.photo:
            photo = message.photo[-1]
        elif message.document:
            photo = message.document
        elif message.reply_to_message and (message.reply_to_message.photo or message.reply_to_message.document):
            photo = message.reply_to_message.photo[-1] if message.reply_to_message.photo else message.reply_to_message.document
        if not photo:
            await processing_msg.edit_text("Изображение для перерисовки не найдено.")
            return
        image_bytes = await download_telegram_image(bot, photo)
        
        # 1. Описываем картинку через Gemini (Input Images работает хорошо)
        detailed_prompt = """Опиши детально все, что видишь на этом изображении. 
Укажи: основные объекты, цвета, стиль, фон, детали. Опиши так, чтобы по этому описанию можно было нарисовать "очень плохой и кривой детский рисунок карандашом"."""
        
        def sync_describe():
            return model.generate_content([
                detailed_prompt,
                {"mime_type": "image/jpeg", "data": image_bytes}
            ]).text.strip()
        
        # Gemini описывает картинку
        description = await asyncio.to_thread(sync_describe)
        logging.info(f"Описание от Gemini: {description}")
        
        # 2. Рисуем через Cloudflare по описанию
        # Добавляем стиль в промпт
        style_prompt = f"Children's drawing style, crayon drawing, bad drawing, scribbles. {description}"
        
        status, data = await generate_image_with_cloudflare(style_prompt)
        
        if status == 'SUCCESS':
            await processing_msg.delete()
            await save_and_send_generated_image(message, data['image_data'], filename="redraw_child.png")
        else:
            await processing_msg.edit_text(f"Ошибка генерации: {data.get('error')}")
    except Exception as e:
        logging.error(f"Ошибка в handle_redraw_command: {e}", exc_info=True)
        await processing_msg.edit_text(f"Ошибка: {str(e)}")


async def handle_edit_command(message: types.Message):
    """Отредактируй - использует Img2Img Cloudflare"""
    processing_msg = None
    try:
        logging.info("[EDIT] Получен запрос на редактирование изображения")
        bot_instance = message.bot
        processing_msg = await message.reply("Применяю магию (Img2Img)...")

        image_obj = None
        if message.photo:
            image_obj = message.photo[-1]
        elif message.document:
            image_obj = message.document
        elif message.reply_to_message and (message.reply_to_message.photo or message.reply_to_message.document):
            image_obj = message.reply_to_message.photo[-1] if message.reply_to_message.photo else message.reply_to_message.document
        
        if not image_obj:
            await processing_msg.edit_text("Не удалось найти изображение для редактирования.")
            return

        image_bytes = await download_telegram_image(bot_instance, image_obj)
        if not image_bytes:
            await processing_msg.edit_text("Не удалось загрузить изображение.")
            return

        prompt = ""
        if message.caption:
            prompt = message.caption.lower().replace("отредактируй", "", 1).strip()
        elif message.text:
            prompt = message.text.lower().replace("отредактируй", "", 1).strip()
        
        if not prompt:
            await processing_msg.edit_text("Напишите, во что превратить картинку. Например: 'отредактируй в стиле киберпанк'")
            return

        # ИСПОЛЬЗУЕМ CLOUDFLARE IMG2IMG
        # Передаем prompt и исходные байты
        status, data = await generate_image_with_cloudflare(prompt, source_image_bytes=image_bytes)

        if status == 'SUCCESS':
            await processing_msg.delete()
            await save_and_send_generated_image(message, data['image_data'], filename="edited_cf.png")
        else:
            await processing_msg.edit_text(f"Ошибка редактирования: {data.get('error')}")

    except Exception as e:
        logging.error(f"[EDIT] Критическая ошибка в handle_edit_command: {e}", exc_info=True)
        if processing_msg:
            await processing_msg.edit_text("Произошла критическая ошибка при редактировании изображения.")
        else:
            await message.reply("Произошла критическая ошибка при редактировании изображения.")


async def handle_kandinsky_generation_command(message: types.Message):
    """Сгенерируй - генерация через Kandinsky (БЕЗ ИЗМЕНЕНИЙ)"""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompt = None
    if message.text.lower().startswith("сгенерируй "):
        prompt = message.text[len("сгенерируй "):].strip()
    elif message.text.lower().strip() == "сгенерируй" and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    if not prompt:
        await message.reply("Что именно сгенерировать?")
        return
    processing_message = await message.reply("Думаю над вашим запросом... 🤖")
    success, error_message, image_data = await process_kandinsky_generation(prompt)
    if success and image_data:
        await processing_message.delete()
        buffered_image = types.BufferedInputFile(image_data, filename="kandinsky.png")
        await message.reply_photo(buffered_image)
    else:
        await processing_message.edit_text(f"Ошибка: {error_message}")
