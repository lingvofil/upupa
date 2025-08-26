import requests
import json
import time
import asyncio
import os
import logging
import random
import base64
from aiogram import types

from config import KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY, bot
from prompts import actions

class FusionBrainAPI:
    """Класс для взаимодействия с API Kandinsky (FusionBrain)."""
    def __init__(self, url, api_key, secret_key):
        self.URL = url
        self.AUTH_HEADERS = {'X-Key': f'Key {api_key}', 'X-Secret': f'Secret {secret_key}'}

    def get_pipeline(self):
        try:
            response = requests.get(self.URL + 'key/api/v1/pipelines', headers=self.AUTH_HEADERS)
            response.raise_for_status()
            data = response.json()
            return data[0]['id'] if data and 'id' in data[0] else None
        except requests.RequestException as e:
            logging.error(f"Ошибка при получении pipeline: {e}")
            return None

    def generate(self, prompt, pipeline, images=1, width=1024, height=1024):
        params = {"type": "GENERATE", "numImages": images, "width": width, "height": height, "generateParams": {"query": f'{prompt}'}}
        data = {'pipeline_id': (None, pipeline), 'params': (None, json.dumps(params), 'application/json')}
        try:
            response = requests.post(self.URL + 'key/api/v1/pipeline/run', headers=self.AUTH_HEADERS, files=data)
            response.raise_for_status()
            data = response.json()
            if 'uuid' in data: return data['uuid'], None
            error_message = data.get('errorDescription') or "Unknown API error"
            logging.error(f"Kandinsky API не вернул UUID. Ответ: {error_message}")
            return None, error_message
        except requests.RequestException as e:
            logging.error(f"HTTP ошибка при запуске генерации: {e}")
            return None, str(e)

    def check_generation(self, request_id, attempts=10, delay=10):
        while attempts > 0:
            try:
                response = requests.get(self.URL + 'key/api/v1/pipeline/status/' + request_id, headers=self.AUTH_HEADERS)
                response.raise_for_status()
                data = response.json()
                if data.get('status') == 'DONE':
                    if data.get('result', {}).get('censored', False): return None, "Изображение было зацензурено."
                    return data.get('result', {}).get('files'), None
                if data.get('status') == 'FAIL': return None, data.get('errorDescription', 'Неизвестная ошибка.')
                attempts -= 1
                time.sleep(delay)
            except requests.RequestException as e:
                logging.error(f"HTTP ошибка при проверке статуса: {e}")
                return None, str(e)
        return None, "Превышено время ожидания ответа от API."

api = FusionBrainAPI('https://api-key.fusionbrain.ai/', KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY)
pipeline_id = api.get_pipeline()

async def process_image_generation(prompt):
    """Основная логика генерации изображения через Kandinsky."""
    if not pipeline_id: return False, "Не удалось получить ID модели от API.", None
    try:
        loop = asyncio.get_event_loop()
        uuid, error = await loop.run_in_executor(None, api.generate, prompt, pipeline_id)
        if error: return False, f"Не удалось запустить генерацию: {error}", None
        
        files, check_error = await loop.run_in_executor(None, api.check_generation, uuid)
        if check_error: return False, f"Ошибка при генерации: {check_error}", None
        
        if not files: return False, "API не вернул файлы", None
        
        # Декодируем из base64, убирая возможный префикс
        base64_data = files[0].split(',')[-1]
        image_data = base64.b64decode(base64_data)
        return True, None, image_data
    except Exception as e:
        logging.error(f"Критическая ошибка в process_image_generation: {e}", exc_info=True)
        return False, f"Критическая ошибка: {repr(e)}", None

async def save_and_send_generated_image(message: types.Message, image_data: bytes):
    """Отправляет сгенерированное изображение из байтов."""
    try:
        buffered_image = types.BufferedInputFile(image_data, filename="generated_image.png")
        await message.reply_photo(buffered_image)
    except Exception as e:
        logging.error(f"Ошибка при отправке фото: {e}")
        await message.reply("Не смог отправить картинку.")

async def handle_generate_command(message: types.Message):
    """Обработчик команды 'сгенерируй' (Kandinsky)."""
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompt = None
    if message.text.lower().strip() == "сгенерируй" and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    elif message.text.lower().startswith("сгенерируй "):
        prompt = message.text[len("сгенерируй "):].strip()
        
    if not prompt:
        await message.reply("Что именно сгенерировать-то?")
        return
        
    processing_message = await message.reply("Ща падажжи, генерирую...")
    success, error_message, image_data = await process_image_generation(prompt)
    
    if success and image_data:
        await processing_message.delete()
        await save_and_send_generated_image(message, image_data)
    else:
        logging.error(f"[Kandinsky Error] {error_message}")
        short_message = "Бля, не получилось сгенерировать. " + (error_message or "Неизвестная ошибка.")
        await processing_message.edit_text(short_message)
