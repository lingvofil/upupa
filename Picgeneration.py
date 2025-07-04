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
from Config import KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY, bot, model
from Prompts import actions
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
        response = requests.get(self.URL + 'key/api/v1/pipelines', headers=self.AUTH_HEADERS)
        response.raise_for_status()
        data = response.json()
        if not data:
            raise RuntimeError("Empty pipelines response")
        return data[0]['id']

    def generate(self, prompt, pipeline_id, images=1, width=1024, height=1024):
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
            'pipeline_id': (None, pipeline_id),
            'params': (None, json.dumps(params), 'application/json')
        }

        response = requests.post(
            self.URL + 'key/api/v1/pipeline/run',
            headers=self.AUTH_HEADERS,
            files=data
        )

        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            raise RuntimeError(f"HTTP error from FusionBrain: {response.status_code} {response.text}") from e

        data = response.json()
        print("FusionBrain generate() response:", data)

        if 'uuid' in data:
            return data['uuid']

        if 'pipeline_status' in data:
            raise RuntimeError(f"Pipeline unavailable: {data['pipeline_status']}")

        if 'error' in data:
            raise RuntimeError(f"API error: {data['error']}")

        raise RuntimeError(f"Unexpected response from FusionBrain: {data}")

    def check_generation(self, request_id, attempts=10, delay=10):
        while attempts > 0:
            response = requests.get(self.URL + 'key/api/v1/pipeline/status/' + request_id, headers=self.AUTH_HEADERS)
            try:
                response.raise_for_status()
            except requests.HTTPError as e:
                raise RuntimeError(f"HTTP error in check_generation: {response.status_code} {response.text}") from e

            data = response.json()
            print("FusionBrain check_generation() response:", data)

            if data.get('status') == 'DONE':
                if 'result' in data and 'files' in data['result']:
                    return data['result']['files']
                else:
                    raise RuntimeError(f"DONE status but no files in result: {data}")

            if data.get('status') == 'FAIL':
                error_description = data.get('errorDescription', 'Unknown error')
                raise RuntimeError(f"Generation failed: {error_description}")

            attempts -= 1
            time.sleep(delay)

        raise TimeoutError("Generation timed out after all attempts")

api = FusionBrainAPI('https://api-key.fusionbrain.ai/', KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY)
pipeline_id = api.get_pipeline()

async def process_image_generation(prompt):
    """
    Основная логика генерации изображения через API.
    Возвращает (Успех, Сообщение об ошибке, Данные изображения)
    """
    try:
        loop = asyncio.get_event_loop()
        uuid = await loop.run_in_executor(None, api.generate, prompt, pipeline_id)
        files = await loop.run_in_executor(None, api.check_generation, uuid)
        
        if not files:
            return False, "Не получилось сгенерировать изображение (таймаут API)", None
            
        image_data_or_url = files[0]
        
        # Проверяем, является ли результат base64 строкой
        if image_data_or_url.startswith('data:image') or image_data_or_url.startswith('/9j/'):
            # Это base64 данные
            try:
                # Если есть префикс data:image, убираем его
                if image_data_or_url.startswith('data:image'):
                    base64_data = image_data_or_url.split(',')[1]
                else:
                    base64_data = image_data_or_url
                
                # Декодируем base64
                image_data = base64.b64decode(base64_data)
                return True, None, image_data
                
            except Exception as e:
                return False, f"Ошибка декодирования base64: {str(e)}", None
        
        else:
            # Это URL, пытаемся загрузить
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_data_or_url) as resp:
                        if resp.status == 200:
                            return True, None, await resp.read()
                        else:
                            return False, f"Не смог забрать картинку с URL: {resp.status}", None
            except Exception as e:
                return False, f"Ошибка загрузки по URL: {str(e)}", None
                
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        logging.error(f"Ошибка в process_image_generation: {error_traceback}")
        return False, f"Ошибка генерации: {repr(e)[:300]}", None

async def save_and_send_generated_image(message: types.Message, image_data: bytes):
    """Сохраняет данные изображения во временный файл и отправляет его."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_file:
            tmp_file.write(image_data)
            tmp_path = tmp_file.name
        
        await message.reply_photo(FSInputFile(tmp_path))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

# =============================================================================
# Вспомогательные функции для работы с изображениями
# =============================================================================

def _get_text_size(font, text):
    """(Внутренняя) Вычисляет размер текста для заданного шрифта."""
    try:
        bbox = font.getbbox(text)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        return width, height
    except AttributeError: # Для старых версий Pillow
        return font.getsize(text)

def _overlay_text_on_image(image_bytes: bytes, text: str) -> str:
    """(Внутренняя) Накладывает текст на изображение."""
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)

    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not os.path.exists(font_path):
        font_path = "arial.ttf" # Fallback for local/Windows
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
# ГЛАВНЫЕ ФУНКЦИИ-ОБРАБОТЧИКИ
# =============================================================================

async def handle_pun_image_command(message: types.Message):
    """
    Полностью обрабатывает команду "скаламбурь": генерирует каламбур,
    парсит его, создает изображение, накладывает текст и отправляет.
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Генерирую хуйню...")

    pun_prompt = """составь каламбурное сочетание слов в одном слове. должно быть пересечение конца первого слова с началом второго. 
    Совпадать должны как минимум две буквы. 
    Не комментируй генерацию.
    Ответ дай строго в формате: "слово1+слово2 = итоговоеслово"
    Например: "манго+голубь = манголубь" """
    
    modified_image_path = None
    try:
        # --- Генерация и парсинг каламбура ---
        def sync_call():
            return model.generate_content(pun_prompt).text.strip()

        pun_word = await asyncio.to_thread(sync_call)
        logging.info(f"Сгенерированный каламбур (raw): {pun_word}")

        parts = pun_word.split('=')
        if len(parts) != 2:
            logging.warning(f"Неверный формат каламбура: {pun_word}. Пробуем повтор.")
            pun_word = await asyncio.to_thread(sync_call)
            logging.info(f"Сгенерированный каламбур (повторно): {pun_word}")
            parts = pun_word.split('=')

        original_words = parts[0].strip() if len(parts) == 2 else pun_word
        final_word = parts[1].strip() if len(parts) == 2 else pun_word
        logging.info(f"Попытка сочетания слов: {original_words} => {final_word}")

        # --- Генерация изображения ---
        success, error_message, image_data = await process_image_generation(original_words)

        if not success or not image_data:
            error_text = f"Ошибка при генерации изображения: {error_message}"
            await processing_msg.edit_text(error_text)
            return

        # --- Наложение текста и отправка ---
        modified_image_path = _overlay_text_on_image(image_data, final_word)
        await message.reply_photo(FSInputFile(modified_image_path))
        
        await processing_msg.delete()

    except Exception as e:
        logging.error(f"Ошибка в handle_pun_image_command: {str(e)}", exc_info=True)
        await processing_msg.edit_text(f"Произошла непредвиденная ошибка: {str(e)[:200]}")
    finally:
        # --- Очистка временных файлов ---
        if modified_image_path and os.path.exists(modified_image_path):
            os.remove(modified_image_path)


async def handle_image_generation_command(message: types.Message):
    """
    Полностью обрабатывает команду "нарисуй": извлекает промпт,
    общается с пользователем, генерирует изображение и отправляет результат.
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))

    prompt = None
    if message.text.lower().strip() == "нарисуй" and message.reply_to_message:
        if message.reply_to_message.text:
            prompt = message.reply_to_message.text.strip()
        elif message.reply_to_message.caption:
            prompt = message.reply_to_message.caption.strip()
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
    elif error_message:
        logging.error(f"[Kandinsky Error] {error_message}")
        short_message = "Бля, не получилось нарисовать. " + (error_message[:3500] + '...' if len(error_message) > 3500 else error_message)
        await processing_message.edit_text(short_message)
        
async def handle_redraw_command(message: types.Message):
    """
    Обрабатывает команду "перерисуй" для фото с подписью и реплая на изображение
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Анализирую тваю мазню...")
    
    try:
        # Определяем источник изображения
        photo = None
        
        # Случай 1: Фото/документ с подписью "перерисуй"
        if message.photo:
            photo = message.photo[-1]
            logging.info("Обрабатываем фото с подписью 'перерисуй'")
        elif message.document:
            photo = message.document
            logging.info("Обрабатываем документ с подписью 'перерисуй'")
        # Случай 2: Реплай на сообщение с изображением
        elif message.reply_to_message:
            if message.reply_to_message.photo:
                photo = message.reply_to_message.photo[-1]
                logging.info("Обрабатываем реплай на фото")
            elif message.reply_to_message.document:
                photo = message.reply_to_message.document
                logging.info("Обрабатываем реплай на документ")
        
        if not photo:
            await processing_msg.edit_text("Изображение для перерисовки не найдено.")
            return
        
        # Загружаем изображение
        from adddescribe import download_telegram_image
        image_bytes = await download_telegram_image(bot, photo)
        
        # Создаем детальный промпт для описания изображения
        detailed_prompt = """Опиши детально все, что видишь на этом изображении. 
Укажи:
- Основные объекты и их расположение
- Цвета и освещение
- Стиль и атмосферу
- Фон и детали
- Людей (если есть) - их позы, одежду, выражения лиц
- Архитектуру или пейзаж
- Любые особенности и мелкие детали

Опиши максимально подробно для воссоздания изображения, должен получиться очень плохо и криво нарисованный рисунок карандашом, как будто рисовал трехлетний ребенок. Весь текст должен вмещаться в один абзац, не более 100 слов"""
        
        # Анализируем изображение с помощью Gemini
        def sync_describe():
            return model.generate_content([
                detailed_prompt,
                {"mime_type": "image/jpeg", "data": image_bytes}
            ]).text.strip()
        
        description = await asyncio.to_thread(sync_describe)
        logging.info(f"Детальное описание для перерисовки: {description}")
        
        # Обновляем статус
        await processing_msg.edit_text("Готовим рамачку...")
        
        # Генерируем новое изображение по описанию
        success, error_message, new_image_data = await process_image_generation(description)
        
        if success and new_image_data:
            await processing_msg.delete()
            await save_and_send_generated_image(message, new_image_data)
        else:
            error_text = f"Ошибка блядь: {error_message}"
            await processing_msg.edit_text(error_text)
            logging.error(f"[Redraw Error] {error_text}")
    
    except Exception as e:
        logging.error(f"Ошибка в handle_redraw_command: {str(e)}", exc_info=True)
        await processing_msg.edit_text(f"Произошла непредвиденная ошибка: {str(e)[:200]}")