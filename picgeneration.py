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
from google.api_core import exceptions as google_exceptions
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from aiogram import types
from aiogram.types import FSInputFile

# Убедитесь, что все зависимости импортированы
# (предполагается, что gemini_generation больше не нужен, но оставлен для бэкапа)
from config import KANDINSKY_API_KEY, KANDINSKY_SECRET_KEY, bot, model, image_model, edit_model, API_TOKEN
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
                logging.debug(f"Kandinsky status: {data.get('status')}. Попыток осталось: {attempts}")
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
if not pipeline_id:
    logging.warning("Не удалось получить pipeline_id для Kandinsky при запуске.")

async def process_image_generation(prompt):
    """
    Основная функция для генерации изображений через Kandinsky.
    Возвращает (success, error_message, image_data)
    """
    if not pipeline_id:
        return False, "Не удалось получить ID модели от API (Kandinsky).", None
    try:
        loop = asyncio.get_event_loop()
        uuid, error = await loop.run_in_executor(None, api.generate, prompt, pipeline_id)
        if error:
            return False, f"Не удалось запустить генерацию: {error}", None
        
        # Увеличиваем время ожидания для Kandinsky (20 попыток по 15 секунд)
        # ИСПРАВЛЕНИЕ: Аргументы (20, 15) передаются позиционно в api.check_generation (attempts, delay)
        files, check_error = await loop.run_in_executor(None, api.check_generation, uuid, 20, 15)
        
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
# БЭКАП ФУНКЦИЙ GEMINI (ЗАКОММЕНТИРОВАНЫ)
# =============================================================================

# async def _handle_pun_image_command_gemini(message: types.Message):
#     await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
#     processing_msg = await message.reply("Генерирую хуйню...")
#     pun_prompt = """составь каламбурное сочетание слов в одном слове. должно быть пересечение конца первого слова с началом второго. 
#     Совпадать должны как минимум две буквы. 
#     Не комментируй генерацию.
#     Ответ дай строго в формате: "слово1+слово2 = итоговоеслово"
#     Например: "манго+голубь = манголубь" """
#     try:
#         def sync_call():
#             return model.generate_content(pun_prompt).text.strip()
#         pun_word = await asyncio.to_thread(sync_call)
#         
#         parts = pun_word.split('=')
#         
#         if len(parts) != 2:
#             await processing_msg.edit_text(f"Не удалось распознать каламбур. Ответ нейросети не соответствует формату 'слово1+слово2 = итоговоеслово'. Ответ: {pun_word}")
#             return
# 
#         source_words = parts[0].strip()
#         final_word = parts[1].strip()
# 
#         # ИЗМЕНЕНИЕ: Промпт сделан более прямым и "машинным", чтобы модель гарантированно генерировала изображение, а не текст.
#         image_gen_prompt = f"Визуализация каламбура '{final_word}'. Сюрреалистичная картина, объединяющая концепции '{source_words}'. Без букв и текста на изображении. Фотореалистичный стиль."
#         
#         status, data = await process_gemini_generation(image_gen_prompt)
# 
#         if status == 'SUCCESS':
#             image_data = data['image_data']
#             # Накладываем на чистое изображение только итоговое слово
#             modified_path = _overlay_text_on_image(image_data, final_word)
#             await message.reply_photo(FSInputFile(modified_path))
#             os.remove(modified_path)
#             await processing_msg.delete()
#         else:
#             # Если data содержит текст ответа, покажем его пользователю
#             error_text = data.get('error')
#             if "Gemini не вернул изображение, но вернул текст" in error_text:
#                 text_response = error_text.split(":", 1)[1].strip()
#                 await processing_msg.edit_text(f"Модель не смогла сгенерировать картинку, но вот что она ответила:\n\n_{text_response}_", parse_mode="Markdown")
#             else:
#                 await processing_msg.edit_text(f"Ошибка генерации: {error_text}")
# 
#     except Exception as e:
#         logging.error(f"Ошибка в _handle_pun_image_command_gemini: {e}", exc_info=True)
#         await processing_msg.edit_text(f"Ошибка: {str(e)}")


# async def _handle_image_generation_command_gemini(message: types.Message):
#     await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
#     prompt = None
#     if message.text.lower().strip() == "нарисуй" and message.reply_to_message:
#         prompt = message.reply_to_message.text or message.reply_to_message.caption
#     elif message.text.lower().startswith("нарисуй "):
#         prompt = message.text[len("нарисуй "):].strip()
#     if not prompt:
#         await message.reply("Шо именно нарисовать-то?")
#         return
#     processing_message = await message.reply("Ща падажжи, рисую...")
#     status, data = await process_gemini_generation(prompt)
#     if status == 'SUCCESS':
#         await processing_message.delete()
#         await save_and_send_gemini(message, data['image_data'])
#     else:
#         await processing_message.edit_text(f"Ошибка: {data.get('error')}")

# async def _handle_redraw_command_gemini(message: types.Message):
#     await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
#     processing_msg = await message.reply("Анализирую тваю мазню...")
#     try:
#         photo = None
#         if message.photo:
#             photo = message.photo[-1]
#         elif message.document:
#             photo = message.document
#         elif message.reply_to_message and (message.reply_to_message.photo or message.reply_to_message.document):
#             photo = message.reply_to_message.photo[-1] if message.reply_to_message.photo else message.reply_to_message.document
#         if not photo:
#             await processing_msg.edit_text("Изображение для перерисовки не найдено.")
#             return
#         image_bytes = await download_telegram_image(bot, photo)
#         detailed_prompt = """Опиши детально все, что видишь на этом изображении. 
# Укажи: основные объекты, цвета, стиль, фон, детали. Опиши максимально подробно для воссоздания изображения, должен получиться очень плохо и криво нарисованный рисунок карандашом, как будто рисовал трехлетний ребенок. Весь текст должен вмещаться в один абзац, не более 100 слов"""
#         def sync_describe():
#             return model.generate_content([
#                 detailed_prompt,
#                 {"mime_type": "image/jpeg", "data": image_bytes}
#             ]).text.strip()
#         description = await asyncio.to_thread(sync_describe)
#         status, data = await process_gemini_generation(description)
#         if status == 'SUCCESS':
#             await processing_msg.delete()
#             await save_and_send_gemini(message, data['image_data'])
#         else:
#             await processing_msg.edit_text(f"Ошибка: {data.get('error')}")
#     except Exception as e:
#         logging.error(f"Ошибка в _handle_redraw_command_gemini: {e}", exc_info=True)
#         await processing_msg.edit_text(f"Ошибка: {str(e)}")

# # ✨ Редактирование изображения через Gemini
# async def _handle_edit_command_gemini(message: types.Message):
#     processing_msg = None
#     try:
#         logging.info("[EDIT] Получен запрос на редактирование изображения")
#         bot_instance = message.bot # Используем bot из message
#         processing_msg = await message.reply("Применяю магию...")
# 
#         # 1. Получаем фото
#         image_obj = None
#         if message.photo:
#             image_obj = message.photo[-1]
#         elif message.document:
#             image_obj = message.document
#         elif message.reply_to_message and (message.reply_to_message.photo or message.reply_to_message.document):
#             image_obj = message.reply_to_message.photo[-1] if message.reply_to_message.photo else message.reply_to_message.document
#         
#         if not image_obj:
#             await processing_msg.edit_text("Не удалось найти изображение для редактирования.")
#             return
# 
#         # 2. Скачиваем изображение в байты
#         image_bytes = await download_telegram_image(bot_instance, image_obj)
#         if not image_bytes:
#              await processing_msg.edit_text("Не удалось загрузить изображение.")
#              return
#         logging.info(f"[EDIT] Изображение загружено, размер {len(image_bytes)} байт")
# 
#         # 3. Получаем текстовый промпт
#         prompt = ""
#         if message.caption:
#             prompt = message.caption.lower().replace("отредактируй", "", 1).strip()
#         elif message.text:
#             prompt = message.text.lower().replace("отредактируй", "", 1).strip()
#     _     
#         if not prompt:
#             await processing_msg.edit_text("Пожалуйста, укажите, как нужно отредактировать изображение. Например: 'отредактируй добавь шляпу'")
#             return
# 
#         # 4. Отправляем запрос в Gemini
#         def sync_edit_call():
#             # Готовим данные для модели: текст и PIL изображение
#             img = Image.open(BytesIO(image_bytes))
#             # ИЗМЕНЕНИЕ: Используем специальную модель для редактирования
#             return edit_model.generate_content([prompt, img])
# 
#         response = await asyncio.to_thread(sync_edit_call)
#         
#         # 5. Обрабатываем ответ
#         edited_image_found = False
#         # Ответ от API состоит из "частей". Ищем часть с изображением.
#         for part in response.parts:
#             # Самый надежный способ - проверить MIME-тип
#             if part.mime_type and part.mime_type.startswith("image/"):
#                 # Извлекаем байты изображения
#                 image_data = part.inline_data.data
#                 output_file = types.BufferedInputFile(image_data, filename="edited.png")
#                 
#                 await processing_msg.delete() # Удаляем сообщение "Применяю магию..."
#                 await message.reply_photo(photo=output_file)
#                 
#                 edited_image_found = True
#                 break # Выходим из цикла, так как нашли картинку
# 
#         if not edited_image_found:
#             # Если изображений в ответе нет, возможно, модель вернула текст (например, с ошибкой или отказом)
#             text_feedback = "Модель не вернула изображение."
#             try:
#                 # Попытаемся извлечь текстовый ответ для отладки
#                 text_feedback = response.text
#                 logging.warning(f"[EDIT] Gemini не вернул изображение. Ответ: {text_feedback}")
#             except Exception as e:
#                 logging.error(f"[EDIT] Не удалось извлечь текст из ответа Gemini: {e}. Полный ответ: {response}")
# 
#             await processing_msg.edit_text(
#                 f"Не удалось получить изменённое изображение. Попробуйте переформулировать запрос.\n\n"
#                 f"Ответ модели: _{text_feedback}_",
#                 parse_mode="Markdown"
#             )
#     # ИЗМЕНЕНИЕ: Отлавливаем ошибку 'Not Found' и даем пользователю четкую инструкцию
#     except google_exceptions.NotFound as e:
#         logging.error(f"[EDIT] Ошибка 'Модель не найдена': {e}", exc_info=True)
#         error_message = (
#             "**Ошибка: Модель для редактирования не найдена!**\n\n"
#             "Похоже, что в `config.py` указано неверное имя модели.\n"
#             "Пожалуйста, замените строку в `config.py` на:\n"
#             "`edit_model = genai.GenerativeModel(\"models/gemini-pro-vision\")`\n\n"
#             "Это специальная модель для работы с изображениями."
#         )
#         if processing_msg:
#             await processing_msg.edit_text(error_message, parse_mode="Markdown")
#         else:
#             await message.reply(error_message, parse_mode="Markdown")
#     except Exception as e:
#         logging.error(f"[EDIT] Критическая ошибка в _handle_edit_command_gemini: {e}", exc_info=True)
#         if processing_msg:
#             await processing_msg.edit_text("Произошла критическая ошибка при редактировании изображения.")
#         else:
#             await message.reply("Произошла критическая ошибка при редактировании изображения.")


# =============================================================================
# Каламбур, Нарисуй, Перерисуй, Отредактируй -> KANDINSKY
# =============================================================================

async def handle_pun_image_command(message: types.Message):
    """
    Генерирует каламбур (текст через Gemini) и картинку (через Kandinsky).
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    processing_msg = await message.reply("Генерирую хуйню...")
    pun_prompt = """составь каламбурное сочетание слов в одном слове. должно быть пересечение конца первого слова с началом второго. 
    Совпадать должны как минимум две буквы. 
    Не комментируй генерацию.
    Ответ дай строго в формате: "слово1+слово2 = итоговоеслово"
    Например: "манго+голубь = манголубь" """
    try:
        # 1. Генерируем текст каламбура (все еще через Gemini)
        def sync_call():
            return model.generate_content(pun_prompt).text.strip()
        pun_word = await asyncio.to_thread(sync_call)
        
        parts = pun_word.split('=')
        
        if len(parts) != 2:
            await processing_msg.edit_text(f"Не удалось распознать каламбур. Ответ нейросети (Gemini) не соответствует формату 'слово1+слово2 = итоговоеслово'. Ответ: {pun_word}")
            return

        source_words = parts[0].strip()
        final_word = parts[1].strip()

        # 2. Генерируем изображение (уже через Kandinsky)
        image_gen_prompt = f"Визуализация каламбура '{final_word}'. Сюрреалистичная картина, объединяющая концепции '{source_words}'. Без букв и текста на изображении. Фотореалистичный стиль, высокое качество."
        
        success, error_message, image_data = await process_image_generation(image_gen_prompt)

        if success and image_data:
            # 3. Накладываем текст на готовое изображение
            modified_path = _overlay_text_on_image(image_data, final_word)
            await message.reply_photo(FSInputFile(modified_path))
            if os.path.exists(modified_path):
                os.remove(modified_path)
            await processing_msg.delete()
        else:
            await processing_msg.edit_text(f"Ошибка генерации изображения (Kandinsky): {error_message}")

    except Exception as e:
        logging.error(f"Ошибка в handle_pun_image_command: {e}", exc_info=True)
        await processing_msg.edit_text(f"Ошибка: {str(e)}")


async def handle_image_generation_command(message: types.Message):
    """
    Команда "Нарисуй" -> Kandinsky
    """
    await bot.send_chat_action(chat_id=message.chat.id, action=random.choice(actions))
    prompt = None
    if message.text.lower().strip() == "нарисуй" and message.reply_to_message:
        prompt = message.reply_to_message.text or message.reply_to_message.caption
    elif message.text.lower().startswith("нарисуй "):
        prompt = message.text[len("нарисуй "):].strip()
    if not prompt:
        await message.reply("Шо именно нарисовать-то?")
        return
        
    processing_message = await message.reply("Ща падажжи, ебана.")
    
    # Вызываем Kandinsky
    success, error_message, image_data = await process_image_generation(prompt)
    
    if success and image_data:
        await processing_message.delete()
        buffered_image = types.BufferedInputFile(image_data, filename="kandinsky.png")
        await message.reply_photo(buffered_image)
    else:
        await processing_message.edit_text(f"Ошибка (Kandinsky): {error_message}")


async def handle_redraw_command(message: types.Message):
    """
    Команда "Перерисуй" -> Kandinsky (с описанием от Gemini)
    """
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

        # 1. Получаем описание изображения (все еще через Gemini, т.к. Kandinsky не видит)
        image_bytes = await download_telegram_image(bot, photo)
        description = ""
        try:
            detailed_prompt = """Опиши детально все, что видишь на этом изображении. 
Укажи: основные объекты, цвета, стиль, фон, детали. Опиши максимально подробно для воссоздания изображения, должен получиться очень плохо и криво нарисованный рисунок карандашом, как будто рисовал трехлетний ребенок. Весь текст должен вмещаться в один абзац, не более 100 слов"""
            
            def sync_describe():
                # Убедимся, что model (gemini) определена
                if not model:
                    raise Exception("Модель Gemini (model) не сконфигурирована.")
                return model.generate_content([
                    detailed_prompt,
                    {"mime_type": "image/jpeg", "data": image_bytes}
                ]).text.strip()
            description = await asyncio.to_thread(sync_describe)
            logging.info(f"[Redraw] Gemini дал описание: {description[:100]}...")
            await processing_msg.edit_text("Анал лизирую твою мазню")
            
        except Exception as e:
            logging.warning(f"Ошибка получения описания от Gemini (в handle_redraw_command): {e}. Используем запасной промпт.")
            description = "очень плохо и криво нарисованный рисунок карандашом, как будто рисовал трехлетний ребенок"
            await processing_msg.edit_text("Не смог получить описание от Gemini, рисую пародию по общему промпту (через Kandinsky)...")

        # 2. Генерируем изображение по описанию (через Kandinsky)
        success, error_message, image_data = await process_image_generation(description)
        
        if success and image_data:
            await processing_msg.delete()
            buffered_image = types.BufferedInputFile(image_data, filename="kandinsky_redraw.png")
            await message.reply_photo(buffered_image)
        else:
            await processing_msg.edit_text(f"Ошибка (Kandinsky): {error_message}")
            
    except Exception as e:
        logging.error(f"Ошибка в handle_redraw_command: {e}", exc_info=True)
        await processing_msg.edit_text(f"Ошибка: {str(e)}")


async def handle_edit_command(message: types.Message):
    """
    Команда "Отредактируй" -> Kandinsky (с описанием от Gemini)
    Kandinsky не умеет редактировать, поэтому мы описываем
    оригинал и добавляем промпт, генерируя новое изображение.
    """
    processing_msg = None
    try:
        logging.info("[EDIT-KANDINSKY] Получен запрос на редактирование")
        bot_instance = message.bot 
        processing_msg = await message.reply("Применяю магию (через Kandinsky)...")

        # 1. Получаем фото
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

        # 2. Скачиваем изображение
        image_bytes = await download_telegram_image(bot_instance, image_obj)
        if not image_bytes:
             await processing_msg.edit_text("Не удалось загрузить изображение.")
             return
        logging.info(f"[EDIT-KANDINSKY] Изображение загружено, размер {len(image_bytes)} байт")

        # 3. Получаем текстовый промпт
        prompt_text = ""
        if message.caption:
            prompt_text = message.caption.lower().replace("отредактируй", "", 1).strip()
        elif message.text:
            prompt_text = message.text.lower().replace("отредактируй", "", 1).strip()
            
        if not prompt_text:
            await processing_msg.edit_text("Пожалуйста, укажите, как нужно отредактировать изображение. Например: 'отредактируй добавь шляпу'")
            return
        
        # 4. Получаем описание оригинала (через Gemini)
        original_description = ""
        try:
            await processing_msg.edit_text("Описываю оригинал (через Gemini)...")
            def sync_describe_original():
                if not model:
                     raise Exception("Модель Gemini (model) не сконфигурирована.")
                return model.generate_content([
                    "Опиши это изображение детально для его воссоздания: объекты, фон, стиль.",
                    {"mime_type": "image/jpeg", "data": image_bytes}
                ]).text.strip()
            original_description = await asyncio.to_thread(sync_describe_original)
            logging.info(f"[EDIT-KANDINSKY] Gemini дал описание: {original_description[:100]}...")
            
        except Exception as e:
            logging.warning(f"Ошибка получения описания от Gemini (в handle_edit_command): {e}. Используем только промпт пользователя.")
            original_description = "" # Оставляем пустым, если Gemini не ответил

        # 5. Собираем финальный промпт для Kandinsky
        if original_description:
            final_kandinsky_prompt = f"{original_description}. {prompt_text}"
        else:
            final_kandinsky_prompt = prompt_text # Если Gemini не смог описать, используем только запрос
            
        await processing_msg.edit_text(f"Генерирую новое изображение по промпту: '{final_kandinsky_prompt[:150]}...' (через Kandinsky)")

        # 6. Генерируем новое изображение (через Kandinsky)
        success, error_message, image_data = await process_image_generation(final_kandinsky_prompt)

        if success and image_data:
            await processing_msg.delete()
            buffered_image = types.BufferedInputFile(image_data, filename="kandinsky_edited.png")
            await message.reply_photo(buffered_image)
        else:
            await processing_msg.edit_text(f"Ошибка генерации (Kandinsky): {error_message}")

    except Exception as e:
        logging.error(f"[EDIT-KANDINSKY] Критическая ошибка: {e}", exc_info=True)
        if processing_msg:
            await processing_msg.edit_text("Произошла критическая ошибка при редактировании изображения.")
        else:
            await message.reply("Произошла критическая ошибка при редактировании изображения.")


# =============================================================================
# Сгенерируй -> Kandinsky (остается без изменений)
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
    processing_message = await message.reply("Думаю над вашим запросом (Kandinsky)... 🤖")
    success, error_message, image_data = await process_image_generation(prompt)
    if success and image_data:
        await processing_message.delete()
        buffered_image = types.BufferedInputFile(image_data, filename="kandinsky.png")
        await message.reply_photo(buffered_image)
    else:
        await processing_message.edit_text(f"Ошибка: {error_message}")

# =============================================================================
# Вспомогательные функции (без изменений)
# =============================================================================

def _get_text_size(font, text):
    try:
        bbox = font.getbbox(text)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        return width, height
    except AttributeError:
        # Fallback for older PIL versions
        return font.getsize(text)

def _overlay_text_on_image(image_bytes: bytes, text: str) -> str:
    output_path = os.path.join(tempfile.gettempdir(), f"modified_pun_{random.randint(1000, 9999)}.jpg")
    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        logging.error(f"Ошибка открытия изображения в _overlay_text_on_image: {e}")
        # Создаем запасное изображение, если исходное повреждено
        image = Image.new('RGB', (1024, 1024), (20, 20, 20))
        text = f"Ошибка рендеринга:\n{text}"

    draw = ImageDraw.Draw(image)
    
    # Поиск шрифта
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    font = None
    font_size = 48
    
    if os.path.exists(font_path):
        font = ImageFont.truetype(font_path, font_size)
    else:
        # Альтернативный путь (может отличаться в вашей системе)
        font_path = "/usr/share/fonts/TTF/DejaVuSans.ttf"
        if os.path.exists(font_path):
            font = ImageFont.truetype(font_path, font_size)
        else:
             # Запасной вариант для Windows (если вдруг)
            font_path = "arial.ttf"
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, font_size)
            else:
                logging.warning("Шрифты (DejaVuSans, arial) не найдены. Используется стандартный шрифт PIL.")
                try:
                    font = ImageFont.load_default()
                except IOError:
                    logging.error("Не удалось загрузить даже стандартный шрифт.")
                    # В этом случае font останется None

    if font is None:
         logging.error("Шрифт не загружен. Наложение текста невозможно. Изображение будет сохранено как есть.")
         image.save(output_path, quality=90)
         return output_path

    max_width = image.width - 40
    
    # Улучшенный расчет средней ширины символа
    sample_chars = "абвгдежзийклмнопрстуфхцчшщъыьэюяАБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
    try:
        avg_char_width = sum(_get_text_size(font, char)[0] for char in sample_chars) / len(sample_chars)
    except Exception:
        avg_char_width = font_size / 2 # Приблизительный fallback
        
    max_chars_per_line = int(max_width // avg_char_width) if avg_char_width > 0 else 20
    
    lines = textwrap.wrap(text, width=max_chars_per_line, drop_whitespace=False, replace_whitespace=False)
    
    if not lines:
        logging.warning("Textwrap не вернул строк, возможно, текст пустой.")
        lines = [""] # Гарантируем хотя бы одну пустую строку для рендера фона

    try:
        _, line_height = _get_text_size(font, "A")
    except Exception as e:
        logging.warning(f"Не удалось получить высоту строки: {e}, используем fallback {font_size}")
        line_height = font_size

    text_block_height = (line_height + 5) * len(lines)
    margin_bottom = 60
    
    # Рисуем полупрозрачный фон для текста
    y = image.height - text_block_height - margin_bottom
    try:
        rectangle = Image.new('RGBA', (image.width, text_block_height + 40), (0, 0, 0, 128))
        image.paste(rectangle, (0, y - 20), rectangle)
    except Exception as e:
        logging.warning(f"Не удалось наложить полупрозрачный фон: {e}")
        # Рисуем сплошной фон, если RGBA не удалось
        draw.rectangle([0, y - 20, image.width, y + text_block_height + 20], fill=(0, 0, 0))

    
    current_y = y - 10
    
    # Рисуем текст
    for line in lines:
        try:
            text_width, _ = _get_text_size(font, line)
        except Exception:
            text_width = len(line) * avg_char_width # Fallback
            
        x = (image.width - text_width) / 2
        # Обводка для читаемости
        draw.text((x-1, current_y-1), line, font=font, fill="black")
        draw.text((x+1, current_y-1), line, font=font, fill="black")
        draw.text((x-1, current_y+1), line, font=font, fill="black")
        draw.text((x+1, current_y+1), line, font=font, fill="black")
        # Сам текст
        draw.text((x, current_y), line, font=font, fill="white")
        current_y += line_height + 5
        
    image.save(output_path, quality=90)
    return output_path
