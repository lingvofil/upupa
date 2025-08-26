import requests
import json
import time
import aiohttp
import asyncio
import tempfile
import os
import logging
import random
import textwrap
import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from aiogram import types
from aiogram.types import FSInputFile

# Убедитесь, что все зависимости импортированы
from config import KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY, bot, model
from prompts import actions
from adddescribe import download_telegram_image

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
        """Получает ID модели (pipeline) для генерации."""
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
        """Отправляет запрос на генерацию изображения."""
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
            response.raise_for_status() # Проверка на HTTP ошибки (4xx, 5xx)
            data = response.json()
            
            # Ключевая проверка: есть ли 'uuid' в ответе?
            if 'uuid' in data:
                return data['uuid'], None  # Возвращаем uuid и отсутствие ошибки
            
            # Если uuid нет, значит, API вернуло ошибку или статус
            error_message = data.get('errorDescription') or data.get('message') or data.get('pipeline_status') or json.dumps(data)
            logging.error(f"Kandinsky API не вернул UUID. Ответ: {error_message}")
            return None, error_message # Возвращаем отсутствие uuid и сообщение об ошибке

        except requests.RequestException as e:
            logging.error(f"HTTP ошибка при запуске генерации: {e}")
            return None, str(e)
        except json.JSONDecodeError:
            logging.error(f"Ошибка декодирования JSON ответа: {response.text}")
            return None, "API вернул некорректный JSON."


    def check_generation(self, request_id, attempts=10, delay=10):
        """Проверяет статус генерации изображения."""
        while attempts > 0:
            try:
                response = requests.get(self.URL + 'key/api/v1/pipeline/status/' + request_id, headers=self.AUTH_HEADERS)
                response.raise_for_status()
                data = response.json()

                if data.get('status') == 'DONE':
                    # Проверка на цензуру
                    if data.get('result', {}).get('censored', False):
                        logging.warning(f"Генерация {request_id} была зацензурена.")
                        return None, "Изображение было зацензурено."
                    return data.get('result', {}).get('files'), None # Возвращаем файлы и отсутствие ошибки

                if data.get('status') == 'FAIL':
                    error_desc = data.get('errorDescription', 'Неизвестная ошибка выполнения.')
                    logging.error(f"Генерация {request_id} провалена с ошибкой: {error_desc}")
                    return None, error_desc # Возвращаем отсутствие файлов и ошибку

                attempts -= 1
                time.sleep(delay)

            except requests.RequestException as e:
                logging.error(f"HTTP ошибка при проверке статуса: {e}")
                return None, str(e)
            except json.JSONDecodeError:
                 logging.error(f"Ошибка декодирования JSON при проверке статуса: {response.text}")
                 # Продолжаем попытки, возможно, это временная ошибка
                 attempts -= 1
                 time.sleep(delay)

        return None, "Превышено время ожидания ответа от API." # Возвращаем отсутствие файлов и ошибку таймаута

# --- Инициализация API ---
api = FusionBrainAPI('https://api-key.fusionbrain.ai/', KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY)
pipeline_id = api.get_pipeline()

async def process_image_generation(prompt):
    """
    Основная логика генерации изображения через API.
    Возвращает (Успех, Сообщение об ошибке, Данные изображения)
    """
    if not pipeline_id:
        return False, "Не удалось получить ID модели от API. Проверьте ключи или доступность сервиса.", None

    try:
        loop = asyncio.get_event_loop()
        
        # 1. Запускаем генерацию
        uuid, error = await loop.run_in_executor(None, api.generate, prompt, pipeline_id)
        
        if error:
            return False, f"Не удалось запустить генерацию: {error}", None
        
        # 2. Проверяем статус
        files, check_error = await loop.run_in_executor(None, api.check_generation, uuid)
        
        if check_error:
            return False, f"Ошибка при генерации: {check_error}", None

        if not files:
            return False, "Не получилось сгенерировать изображение (API не вернул файлы)", None
            
        image_data_base64 = files[0]
        
        # 3. Декодируем изображение из base64
        try:
            # Убираем префикс, если он есть
            if ',' in image_data_base64:
                base64_data = image_data_base64.split(',')[1]
            else:
                base64_data = image_data_base64
            
            image_data = base64.b64decode(base64_data)
            return True, None, image_data
            
        except Exception as e:
            logging.error(f"Ошибка декодирования base64: {e}")
            return False, f"Ошибка декодирования полученного изображения: {str(e)}", None
                
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        logging.error(f"Критическая ошибка в process_image_generation: {error_traceback}")
        return False, f"Критическая ошибка: {repr(e)[:300]}", None

async def save_and_send_generated_image(message: types.Message, image_data: bytes):
    """Сохраняет данные изображения во временный файл и отправляет его."""
    tmp_path = None
    try:
        # Используем BufferedInputFile для отправки из памяти без сохранения на диск
        buffered_image = types.BufferedInputFile(image_data, filename="generated_image.png")
        await message.reply_photo(buffered_image)
    except Exception as e:
        logging.error(f"Ошибка при отправке фото: {e}")
        await message.reply("Не смог отправить картинку, что-то пошло не так.")


# =============================================================================
# Вспомогательные функции (остаются без изменений)
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
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)

    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not os.path.exists(font_path):
        font_path = "arial.ttf"
    font_size = 48
    font = ImageFont.truetype(font_path, font_size)

    max_width = image.width - 40
    
    sample_chars = "абвгдежзийклмнопрстуфхцчшщъыьэюя"
    avg_char_width = sum(_get_text_size(font, char)[0] for char in sample_chars) / len(sample_chars)
    max_chars_per_line = int(max_width // avg_char_width) if avg_char_width > 0 else 20

    lines = textwrap.wrap(text, width=max_chars_per_line)
    _, line_height = _get_text_size(font, "A")
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

    output_path = "modified_pun_image.jpg"
    image.save(output_path)
    return output_path

# =============================================================================
# ГЛАВНЫЕ ФУНКЦИИ-ОБРАБОТЧИКИ (логика остается прежней, но теперь получает более точные ошибки)
# =============================================================================

async def handle_pun_image_command(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Генерирую хуйню...")

    pun_prompt = """составь каламбурное сочетание слов в одном слове. должно быть пересечение конца первого слова с началом второго. 
    Совпадать должны как минимум две буквы. 
    Не комментируй генерацию.
    Ответ дай строго в формате: "слово1+слово2 = итоговоеслово"
    Например: "манго+голубь = манголубь" """
    
    modified_image_path = None
    try:
        def sync_call():
            return model.generate_content(pun_prompt).text.strip()

        pun_word = await asyncio.to_thread(sync_call)
        logging.info(f"Сгенерированный каламбур (raw): {pun_word}")

        parts = pun_word.split('=')
        if len(parts) != 2:
            logging.warning(f"Неверный формат каламбура: {pun_word}.")
            original_words = pun_word
            final_word = pun_word
        else:
            original_words = parts[0].strip()
            final_word = parts[1].strip()
        
        logging.info(f"Попытка сочетания слов: {original_words} => {final_word}")

        success, error_message, image_data = await process_image_generation(original_words)

        if not success or not image_data:
            error_text = f"Ошибка при генерации изображения: {error_message}"
            await processing_msg.edit_text(error_text)
            return

        modified_image_path = _overlay_text_on_image(image_data, final_word)
        await message.reply_photo(FSInputFile(modified_image_path))
        
        await processing_msg.delete()

    except Exception as e:
        logging.error(f"Ошибка в handle_pun_image_command: {str(e)}", exc_info=True)
        await processing_msg.edit_text(f"Произошла непредвиденная ошибка: {str(e)[:200]}")
    finally:
        if modified_image_path and os.path.exists(modified_image_path):
            os.remove(modified_image_path)


async def handle_image_generation_command(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))

    prompt = None
    if message.text.lower().strip() == "нарисуй" and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    elif message.text.lower().startswith("нарисуй "):
        prompt = message.text[len("нарисуй "):].strip()

    if not prompt:
        await message.reply("Шо именно нарисовать-то, ебалай?")
        return

    processing_message = await message.reply("Ща падажжи ебана, рисую...")
    success, error_message, image_data = await process_image_generation(prompt)
    
    if success and image_data:
        await processing_message.delete()
        await save_and_send_generated_image(message, image_data)
    else:
        logging.error(f"[Kandinsky Error] {error_message}")
        short_message = "Бля, не получилось нарисовать. " + (error_message or "Неизвестная ошибка.")
        await processing_message.edit_text(short_message)
        
async def handle_redraw_command(message: types.Message):
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
        
        detailed_prompt = """Опиши детально все, что видишь на этом изображении. 
Укажи: основные объекты, цвета, стиль, фон, детали. Опиши максимально подробно для воссоздания изображения, должен получиться очень плохо и криво нарисованный рисунок карандашом, как будто рисовал трехлетний ребенок. Весь текст должен вмещаться в один абзац, не более 100 слов"""
        
        def sync_describe():
            return model.generate_content([
                detailed_prompt,
                {"mime_type": "image/jpeg", "data": image_bytes}
            ]).text.strip()
        
        description = await asyncio.to_thread(sync_describe)
        logging.info(f"Детальное описание для перерисовки: {description}")
        
        await processing_msg.edit_text("Готовим рамачку...")
        
        success, error_message, new_image_data = await process_image_generation(description)
        
        if success and new_image_data:
            await processing_msg.delete()
            await save_and_send_generated_image(message, new_image_data)
        else:
            error_text = f"Ошибка блядь: {error_message or 'Неизвестная ошибка.'}"
            await processing_msg.edit_text(error_text)
            logging.error(f"[Redraw Error] {error_text}")
    
    except Exception as e:
        logging.error(f"Ошибка в handle_redraw_command: {str(e)}", exc_info=True)
        await processing_msg.edit_text(f"Произошла непредвиденная ошибка: {str(e)[:200]}")
