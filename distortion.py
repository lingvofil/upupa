import os
import asyncio
import json
import random
import logging
import re
import subprocess
import shutil
import numpy as np
from aiogram import types, Bot
from aiogram.types import FSInputFile
from PIL import Image
import multiprocessing

# Попробуем импортировать seam_carving
try:
    import seam_carving
    SEAM_CARVING_AVAILABLE = True
except ImportError:
    logging.warning("Модуль 'seam_carving' не найден. Функции seam carving будут недоступны.")
    SEAM_CARVING_AVAILABLE = False

# Импортируем общие функции из вашего проекта
from config import bot as main_bot_instance
from whatisthere import download_file

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- ОГРАНИЧЕНИЯ РЕСУРСОВ ---
MAX_AUDIO_DURATION = 180 # 3 минуты

# --- Вспомогательные функции ---

def map_intensity(intensity: int, out_min: float, out_max: float) -> float:
    return out_min + (intensity / 100.0) * (out_max - out_min)

def parse_intensity_from_text(text: str | None) -> int:
    if not text: return 45
    match = re.search(r'\b(\d+)\b', text)
    if match: return max(0, min(100, int(match.group(1))))
    return 45

async def run_ffmpeg_command(command: list[str]) -> tuple[bool, str]:
    logging.info(f"Запуск FFmpeg: {' '.join(command)}")
    process = await asyncio.create_subprocess_exec(
        *command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        error_message = stderr.decode(errors='ignore').strip()
        logging.error(f"FFmpeg ошибка: {error_message}")
        return False, error_message
    return True, "Success"

async def get_media_info(file_path: str) -> dict | None:
    command = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-show_format', '-i', file_path]
    process = await asyncio.create_subprocess_exec(*command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = await process.communicate()
    if process.returncode != 0: return None
    try: return json.loads(stdout.decode(errors='ignore'))
    except json.JSONDecodeError: return None

# --- Функции искажения ---

def distort_text(text: str, intensity: int) -> str:
    chars = list(text)
    num_changes = int(len(chars) * (intensity / 100.0))
    for _ in range(num_changes):
        idx = random.randint(0, len(chars) - 1)
        if chars[idx].isspace(): continue
        action = random.randint(0, 2)
        if action == 0: chars[idx] = chars[idx].swapcase()
        elif action == 1: chars[idx] += random.choice([chr(c) for c in range(0x0300, 0x036F)])
        else: chars.insert(idx, random.choice("!@#$%^&*()_+-=[]{}|;:,.<>?"))
    return "".join(chars)

async def apply_ffmpeg_audio_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    """
    Искажает аудио, используя vibrato как основной эффект.
    """
    # Частота вибрато (дрожания) от 4 до 12 Гц
    vibrato_freq = map_intensity(intensity, 4.0, 12.0)
    # Глубина вибрато (сила эффекта) от 0.1 до 1.0 (максимум)
    vibrato_depth = map_intensity(intensity, 0.1, 1.0)
    
    filters = [f"vibrato=f={vibrato_freq:.2f}:d={vibrato_depth:.2f}"]
    
    # Добавляем другие эффекты на высоких значениях интенсивности
    if intensity > 50:
        crush = map_intensity(intensity, 0.1, 0.5)
        filters.append(f"acrusher=bits=8:mode=log:mix={crush}")
        
    if intensity > 75:
        decay = map_intensity(intensity, 0.1, 0.4)
        delay = map_intensity(intensity, 20, 100)
        filters.append(f"aecho=0.8:0.9:{delay}:{decay}")

    cmd = ['ffmpeg', '-i', input_path, '-af', ",".join(filters), '-c:a', 'libmp3lame', '-q:a', '4', '-y', output_path]
    success, _ = await run_ffmpeg_command(cmd)
    return success

def _seam_carving_blocking_task(src_np, original_w, original_h, new_w, new_h, out_path):
    dst = seam_carving.resize(src_np, (new_w, new_h), energy_mode='backward', order='width-first')
    Image.fromarray(dst).resize((original_w, original_h), Image.LANCZOS).save(out_path, "JPEG", quality=85)

async def apply_seam_carving_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    if not SEAM_CARVING_AVAILABLE: return False
    try:
        distort_percent = max(0, min(intensity, 95))
        with Image.open(input_path) as img:
            if img.width < 50 or img.height < 50: return False
            original_width, original_height = img.size
            src = np.array(img.convert("RGB"))
        new_width = max(int(original_width * (100 - distort_percent) / 100), 20)
        new_height = max(int(original_height * (100 - distort_percent) / 100), 20)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _seam_carving_blocking_task, src, original_width, original_height, new_width, new_height, output_path)
        return True
    except Exception as e:
        logging.error(f"Ошибка seam carving: {e}", exc_info=True)
        return False

# --- ИЗОЛИРОВАННЫЙ ПРОЦЕСС ОБРАБОТКИ ---

async def distortion_worker_async(bot_token: str, chat_id: int, media_info: dict, intensity: int):
    """Асинхронная часть воркера, которая выполняет всю работу."""
    bot_instance = Bot(token=bot_token)
    
    media_type = media_info['media_type']
    input_path = media_info.get('local_path')
    output_path = None
    
    try:
        if media_type in ['audio', 'voice']:
            info = await get_media_info(input_path)
            if not info or 'format' not in info or 'duration' not in info['format']:
                await bot_instance.send_message(chat_id, "Не удалось прочитать информацию о файле. Возможно, он поврежден.")
                raise Exception("Failed to get media info")

            duration = float(info['format']['duration'])
            if duration > MAX_AUDIO_DURATION:
                await bot_instance.send_message(chat_id, f"Слишком длинный аудиофайл ({duration:.1f}с > {MAX_AUDIO_DURATION}с).")
                raise Exception("Duration limit exceeded")

        success, final_media_type = False, None
        
        if media_type == 'text':
            distorted_text = distort_text(media_info['text'], intensity)
            await bot_instance.send_message(chat_id, distorted_text)
            return

        elif media_type in ['photo', 'sticker_static']:
            output_path = f"{input_path}_out.jpg"
            success = await apply_seam_carving_distortion(input_path, output_path, intensity)
            final_media_type = 'photo'

        elif media_type in ['audio', 'voice']:
            output_path = f"{input_path}_out.mp3"
            success = await apply_ffmpeg_audio_distortion(input_path, output_path, intensity)
            final_media_type = media_type

        if success and output_path and os.path.exists(output_path):
            file_to_send = FSInputFile(output_path)
            if final_media_type == 'photo': await bot_instance.send_photo(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'audio': await bot_instance.send_audio(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'voice': await bot_instance.send_voice(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
        else:
            if 'duration' not in locals() or duration <= MAX_AUDIO_DURATION:
                 await bot_instance.send_message(chat_id, "Что-то пошло не так во время искажения.")

    except Exception as e:
        logging.error(f"Ошибка в воркере: {e}", exc_info=True)
        if "limit exceeded" not in str(e).lower():
            try: await bot_instance.send_message(chat_id, "Произошла внутренняя ошибка при обработке.")
            except Exception as send_e: logging.error(f"Не удалось отправить сообщение об ошибке: {send_e}")
    finally:
        if input_path and os.path.dirname(input_path).startswith("temp_worker_"):
            shutil.rmtree(os.path.dirname(input_path), ignore_errors=True)
        await bot_instance.session.close()

def distortion_worker_proc(bot_token: str, chat_id: int, media_info: dict, intensity: int):
    """Точка входа для нового процесса. Запускает асинхронный воркер."""
    logging.info(f"Запущен новый процесс для искажения (PID: {os.getpid()})")
    asyncio.run(distortion_worker_async(bot_token, chat_id, media_info, intensity))

# --- Фильтр и основной обработчик ---

def is_distortion_command(message: types.Message) -> bool:
    try:
        from config import BLOCKED_USERS
        if message.from_user.id in BLOCKED_USERS: return False
        text_to_check = message.caption or message.text
        if text_to_check and "дисторшн" in text_to_check.lower():
            target = message.reply_to_message or message
            return bool(target.photo or target.sticker or target.audio or target.voice or target.text)
        return False
    except Exception: return False

async def handle_distortion_request(message: types.Message):
    """
    Основной обработчик. Скачивает файл и запускает искажение в отдельном процессе.
    """
    try:
        target_message = message.reply_to_message or message
        text_for_parsing = message.text if message.text else message.caption
        intensity = parse_intensity_from_text(text_for_parsing)
        
        media_info = {}
        file_to_download = None
        
        if target_message.photo: 
            media_info = {'media_type': 'photo', 'ext': '.jpg'}
            file_to_download = target_message.photo[-1]
        elif target_message.sticker:
            if target_message.sticker.is_animated or target_message.sticker.is_video:
                await message.answer("Извини, анимированные стикеры и видео-стикеры я больше не искажаю.")
                return
            else: 
                media_info = {'media_type': 'sticker_static', 'ext': '.webp'}
                file_to_download = target_message.sticker
        elif target_message.audio: 
            media_info = {'media_type': 'audio', 'ext': '.mp3'}
            file_to_download = target_message.audio
        elif target_message.voice: 
            media_info = {'media_type': 'voice', 'ext': '.ogg'}
            file_to_download = target_message.voice
        elif target_message.text: 
            media_info = {'media_type': 'text', 'text': target_message.text}
        elif target_message.video or target_message.animation:
            await message.answer("Извини, видео и гифки я больше не искажаю.")
            return

        if not media_info:
            await message.answer("Не нашел, что искажать.")
            return

        if file_to_download:
            temp_dir = f"temp_worker_{random.randint(1000, 9999)}"
            os.makedirs(temp_dir, exist_ok=True)
            local_path = os.path.join(temp_dir, f"input{media_info['ext']}")
            
            if not await download_file(file_to_download.file_id, local_path):
                 await message.answer("Не смог скачать файл.")
                 shutil.rmtree(temp_dir, ignore_errors=True)
                 return
            media_info['local_path'] = local_path

        await message.answer("🌀 ща, сука...")
        
        try:
            multiprocessing.set_start_method("spawn", force=True)
        except RuntimeError:
            pass
            
        proc = multiprocessing.Process(target=distortion_worker_proc, args=(main_bot_instance.token, message.chat.id, media_info, intensity))
        proc.start()

    except Exception as e:
        logging.error(f"Ошибка в handle_distortion_request: {e}", exc_info=True)
        await message.answer("Не удалось запустить обработку.")
