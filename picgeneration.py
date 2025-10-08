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
import google.generativeai as genai
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from aiogram import types
from aiogram.types import FSInputFile

# Убедитесь, что все зависимости импортированы
from config import KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY, bot, model, image_model, API_TOKEN
from prompts import actions
from adddescribe import download_telegram_image
from gemini_generation import process_gemini_generation, save_and_send_generated_image as save_and_send_gemini

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

async def process_image_generation(prompt):
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
        logging.error(f"Критическая ошибка в process_image_generation: {traceback.format_exc()}")
        return False, f"Критическая ошибка: {repr(e)[:300]}", None

# =============================================================================
# Каламбур, Нарисуй, Перерисуй, Отредактируй -> Gemini
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
            await processing_msg.edit_text(f"Не удалось распознать каламбур. Ответ нейросети не соответствует формату 'слово1+слово2 = итоговоеслово'. Ответ: {pun_word}")
            return

        source_words = parts[0].strip()
        final_word = parts[1].strip()

        # ИЗМЕНЕНИЕ: Промпт сделан более прямым и "машинным", чтобы модель гарантированно генерировала изображение, а не текст.
        image_gen_prompt = f"Визуализация каламбура '{final_word}'. Сюрреалистичная картина, объединяющая концепции '{source_words}'. Без букв и текста на изображении. Фотореалистичный стиль."
        
        status, data = await process_gemini_generation(image_gen_prompt)

        if status == 'SUCCESS':
            image_data = data['image_data']
            # Накладываем на чистое изображение только итоговое слово
            modified_path = _overlay_text_on_image(image_data, final_word)
            await message.reply_photo(FSInputFile(modified_path))
            os.remove(modified_path)
            await processing_msg.delete()
        else:
            # Если data содержит текст ответа, покажем его пользователю
            error_text = data.get('error')
            if "Gemini не вернул изображение, но вернул текст" in error_text:
                text_response = error_text.split(":", 1)[1].strip()
                await processing_msg.edit_text(f"Модель не смогла сгенерировать картинку, но вот что она ответила:\n\n_{text_response}_", parse_mode="Markdown")
            else:
                await processing_msg.edit_text(f"Ошибка генерации: {error_text}")

    except Exception as e:
        logging.error(f"Ошибка в handle_pun_image_command: {e}", exc_info=True)
        await processing_msg.edit_text(f"Ошибка: {str(e)}")


async def handle_image_generation_command(message: types.Message):
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompt = None
    if message.text.lower().strip() == "нарисуй" and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    elif message.text.lower().startswith("нарисуй "):
        prompt = message.text[len("нарисуй "):].strip()
    if not prompt:
        await message.reply("Шо именно нарисовать-то?")
        return
    processing_message = await message.reply("Ща падажжи, рисую...")
    status, data = await process_gemini_generation(prompt)
    if status == 'SUCCESS':
        await processing_message.delete()
        await save_and_send_gemini(message, data['image_data'])
    else:
        await processing_message.edit_text(f"Ошибка: {data.get('error')}")

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
        status, data = await process_gemini_generation(description)
        if status == 'SUCCESS':
            await processing_msg.delete()
            await save_and_send_gemini(message, data['image_data'])
        else:
            await processing_msg.edit_text(f"Ошибка: {data.get('error')}")
    except Exception as e:
        logging.error(f"Ошибка в handle_redraw_command: {e}", exc_info=True)
        await processing_msg.edit_text(f"Ошибка: {str(e)}")

# ✨ Редактирование изображения через Gemini
async def handle_edit_command(image_bytes: bytes, prompt: str):
    try:
        print("[EDIT] Получен запрос на редактирование изображения")
        # Используем правильные модальности: IMAGE + TEXT
        response = image_model.generate_content(
            [
                content_types.Image.from_bytes(image_bytes),
                prompt,
            ],
            generation_config={
                "response_modalities": ["IMAGE", "TEXT"],  # важно указать IMAGE + TEXT
            }
        )

        if not response or not hasattr(response, "image"):
            raise ValueError("Ответ не содержит изображения.")

        return response.image

    except Exception as e:
        print(f"[EDIT] Ошибка при редактировании изображения: {e}")
        return None


# =============================================================================
# Сгенерируй -> Kandinsky
# =============================================================================

async def handle_kandinsky_generation_command(message: types.Message):
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
    success, error_message, image_data = await process_image_generation(prompt)
    if success and image_data:
        await processing_message.delete()
        buffered_image = types.BufferedInputFile(image_data, filename="kandinsky.png")
        await message.reply_photo(buffered_image)
    else:
        await processing_message.edit_text(f"Ошибка: {error_message}")

# =============================================================================
# Вспомогательные функции
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
