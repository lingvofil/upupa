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
import tempfile
from concurrent.futures import ThreadPoolExecutor

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
MAX_AUDIO_DURATION = 180  # 3 минуты
MAX_VIDEO_DURATION = 60   # 1 минута для видео
MAX_FILE_SIZE = 20_000_000  # 20MB

# --- Вспомогательные функции ---

def map_intensity(intensity: int, out_min: float, out_max: float) -> float:
    return out_min + (intensity / 100.0) * (out_max - out_min)

def parse_intensity_from_text(text: str | None) -> int:
    if not text: return 45
    match = re.search(r'\b(\d+)\b', text)
    if match: return max(0, min(100, [jg:пароль_(regexp)_190]1))))
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

async def get_video_frame_rate(file_path: str) -> tuple[str, float]:
    """Получает частоту кадров и длительность видео"""
    info = await get_media_info(file_path)
    if not info:
        return "30/1", 0.0
    
    duration = 0.0
    frame_rate = "30/1"
    
    if 'format' in info and 'duration' in info['format']:
        duration = float(info['format']['duration'])
    
    for stream in info.get('streams', []):
        if stream.get('codec_type') == 'video':
            if 'avg_frame_rate' in stream and stream['avg_frame_rate'] != '0/0':
                frame_rate = stream['avg_frame_rate']
            elif 'r_frame_rate' in stream and stream['r_frame_rate'] != '0/0':
                frame_rate = stream['r_frame_rate']
            break
    
    return frame_rate, duration

# --- Функции искажения ---

def distort_text(text: str, intensity: int) -> str:
    chars = list(text)
    num_changes = int(len(chars) * (intensity / 100.0))
    for _ in range(num_changes):
        idx = random.randint(0, len(chars) - 1)
        if chars[idx].isspace(): continue
        action = random.randint(0, 2)
        if action == 0: chars[idx] = chars[idx].swapcase()
        elif action == 1: chars[idx] += random.choice([chr(c) for c in range(0x0300, [jg:пароль_(regexp)_191]
        else: chars.insert(idx, random.choice("!@#$%^&*()_+-=[]{}|;:,.<>?"))
    return "".join(chars)

async def apply_ffmpeg_audio_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    """Искажает аудио, используя vibrato как основной эффект."""
    vibrato_freq = map_intensity(intensity, 4.0, 12.0)
    vibrato_depth = map_intensity(intensity, 0.2, 2.0)
    
    filters = [jg:пароль_(regexp)_192]brato_freq:.2f}:[jg:пароль_(regexp)_193]:.2f}"]
    
    if intensity > 50:
        crush = map_intensity(intensity, 0.2, 1.0)
        [jg:пароль_(regexp)_194]"acrusher=bits=8:mode=log:mix={crush}")
        
    if intensity > 75:
        decay = map_intensity(intensity, 0.1, 0.4)
        delay = map_intensity(intensity, 20, 200)
        [jg:пароль_(regexp)_195]"aecho=0.8:0.9:{delay}:{decay}")

    cmd = ['ffmpeg', '-i', input_path, '-af', ",".join(filters), '-c:a', 'libmp3lame', '-q:a', '4', '-y', output_path]
    success, _ = await run_ffmpeg_command(cmd)
    return success

def _seam_carving_blocking_task(src_np, original_w, original_h, new_w, new_h, out_path):
    dst = seam_carving.resize(src_np, (new_w, new_h), energy_mode='backward', order='width-first')
    Image.fromarray(dst).resize((original_w, original_h), Image.LANCZOS).save(out_path, "JPEG", [jg:пароль_(regexp)_196]

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

def distort_image_sync(image_path: str) -> bool:
    """Синхронная функция для искажения одного кадра"""
    try:
        with Image.open(image_path) as img:
            # Простое искажение без seam carving для скорости
            width, height = img.size
            
            # Случайное изменение размера и возврат к оригиналу
            scale_factor = random.uniform(0.7, 0.9)
            new_width = int(width * scale_factor)
            new_height = int(height * scale_factor)
            
            # Искажаем и возвращаем к оригинальному размеру
            distorted = img.resize((new_width, new_height), Image.LANCZOS)
            distorted = distorted.resize((width, height), Image.NEAREST)
            
            # Добавляем цветовые искажения
            pixels = np.array(distorted)
            noise = [jg:авторизационный_токен_197](-20, 20, pixels.shape, [jg:пароль_(regexp)_198]
            pixels = np.clip(pixels.astype(np.int16) + noise, 0, [jg:пароль_(regexp)_199]int8)
            
            distorted = Image.fromarray(pixels)
            distorted.save(image_path, "PNG")
            return True
    except Exception as e:
        logging.error(f"Ошибка искажения кадра {image_path}: {e}")
        return False

async def extract_frames_from_video(input_path: str, frames_dir: str, frame_rate: str) -> bool:
    """Извлекает кадры из видео"""
    frame_pattern = [jg:авторизационный_токен_200](frames_dir, [jg:пароль_(regexp)_201])
    cmd = ['ffmpeg', '-i', input_path, '-r', frame_rate, frame_pattern, '-y']
    success, _ = await run_ffmpeg_command(cmd)
    return success

async def distort_frames_parallel(frames_dir: str, progress_callback=None) -> bool:
    """Искажает кадры параллельно"""
    try:
        frame_files = [f for f in os.listdir(frames_dir) if f.endswith('.png')]
        frame_files.sort()
        
        if not frame_files:
            return False
        
        total_frames = len(frame_files)
        processed = 0
        
        # Используем ThreadPoolExecutor для параллельной обработки
        with ThreadPoolExecutor(max_workers=min(4, multiprocessing.cpu_count())) as executor:
            loop = asyncio.get_running_loop()
            
            for frame_file in frame_files:
                frame_path = [jg:авторизационный_токен_202](frames_dir, frame_file)
                await loop.run_in_executor(executor, distort_image_sync, frame_path)
                processed += 1
                
                if progress_callback and processed % 10 == 0:  # Обновляем прогресс каждые 10 кадров
                    await progress_callback(processed, total_frames)
        
        return True
    except Exception as e:
        logging.error(f"Ошибка искажения кадров: {e}")
        return False

async def collect_frames_to_video(frames_dir: str, output_path: str, frame_rate: str, media_type: str) -> bool:
    """Собирает кадры обратно в видео"""
    frame_pattern = [jg:авторизационный_токен_203](frames_dir, [jg:пароль_(regexp)_204])
    
    if media_type == 'animation':  # GIF
        cmd = ['ffmpeg', '-r', frame_rate, '-i', frame_pattern, '-f', 'mp4', 
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', output_path, '-y']
    elif media_type == 'video_note':  # Кружочки
        cmd = ['ffmpeg', '-r', frame_rate, '-i', frame_pattern, '-f', 'mp4',
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', output_path, '-y']
    elif media_type == 'sticker_video':  # Видеостикеры
        cmd = ['ffmpeg', '-r', frame_rate, '-i', frame_pattern, '-f', 'webm',
               '-c:v', 'libvpx-vp9', '-b:v', '85k', '-pix_fmt', 'yuva420p', '-an', output_path, '-y']
    else:  # Обычное видео
        cmd = ['ffmpeg', '-r', frame_rate, '-i', frame_pattern, '-f', 'mp4',
               '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', output_path, '-y']
    
    success, _ = await run_ffmpeg_command(cmd)
    return success

async def distort_video_with_audio(input_path: str, output_path: str, intensity: int, media_type: str, progress_callback=None) -> bool:
    """Искажает видео с аудио (если есть)"""
    try:
        temp_dir = tempfile.mkdtemp()
        frames_dir = [jg:авторизационный_токен_205](temp_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        
        # Получаем информацию о видео
        frame_rate, duration = await get_video_frame_rate(input_path)
        
        if progress_callback:
            await progress_callback("Извлекаю кадры...")
        
        # Извлекаем кадры
        if not await extract_frames_from_video(input_path, frames_dir, frame_rate):
            return False
        
        if progress_callback:
            await progress_callback("Искажаю кадры...")
        
        # Искажаем кадры
        async def frame_progress(processed, total):
            if progress_callback:
                progress = int((processed / total) * 70) + 20  # 20-90% прогресса
                await progress_callback(f"Обработано кадров: {processed}/{total} ({progress}%)")
        
        if not await distort_frames_parallel(frames_dir, frame_progress):
            return False
        
        if progress_callback:
            await progress_callback("Собираю видео...")
        
        # Создаем временный файл для видео без звука
        temp_video = [jg:авторизационный_токен_206](temp_dir, [jg:пароль_(regexp)_207])
        if not await collect_frames_to_video(frames_dir, temp_video, frame_rate, media_type):
            return False
        
        # Проверяем, есть ли аудио в оригинале
        info = await get_media_info(input_path)
        has_audio = False
        if info:
            for stream in info.get('streams', []):
                if stream.get('codec_type') == 'audio':
                    has_audio = True
                    break
        
        if has_audio and media_type not in ['sticker_video', 'video_note']:
            # Искажаем аудио и объединяем
            temp_audio = [jg:авторизационный_токен_208](temp_dir, [jg:пароль_(regexp)_209])
            if await apply_ffmpeg_audio_distortion(input_path, temp_audio, intensity):
                # Объединяем видео и аудио
                cmd = ['ffmpeg', '-i', temp_video, '-i', temp_audio, 
                       '-c:v', 'copy', '-c:a', 'copy', output_path, '-y']
                success, _ = await run_ffmpeg_command(cmd)
            else:
                # Если не удалось обработать аудио, копируем оригинальное
                cmd = ['ffmpeg', '-i', temp_video, '-i', input_path, 
                       '-c:v', 'copy', '-c:a', 'copy', '-map', '0:v:0', '-map', '1:a:0', 
                       output_path, '-y']
                success, _ = await run_ffmpeg_command(cmd)
        else:
            # Просто копируем видео без звука
            shutil.copy2(temp_video, output_path)
            success = True
        
        # Очищаем временные файлы
        shutil.rmtree(temp_dir, ignore_errors=True)
        return success
        
    except Exception as e:
        logging.error(f"Ошибка искажения видео: {e}")
        return False

# --- ИЗОЛИРОВАННЫЙ ПРОЦЕСС ОБРАБОТКИ ---

async def distortion_worker_async(bot_token: str, chat_id: int, media_info: dict, intensity: int):
    """Асинхронная часть воркера, которая выполняет всю работу."""
    bot_instance = Bot(token=bot_token)
    progress_message = None
    
    media_type = media_info['media_type']
    input_path = media_info.get('local_path')
    output_path = None
    
    async def update_progress(text):
        nonlocal progress_message
        try:
            if progress_message is None:
                progress_message = await bot_instance.send_message(chat_id, f"🌀 {text}")
            else:
                # Исправленный вызов edit_message_text
                await bot_instance.edit_message_text(
                    text=f"🌀 {text}",
                    chat_id=chat_id,
                    message_id=progress_message.message_id
                )
        except Exception as e:
            logging.error(f"Ошибка обновления прогресса: {e}")
    
    try:
        # Проверяем ограничения для видео/аудио
        if media_type in ['audio', 'voice', 'video', 'animation', 'video_note', 'sticker_video']:
            info = await get_media_info(input_path)
            if not info or 'format' not in info:
                await bot_instance.send_message(chat_id, "Не удалось прочитать информацию о файле.")
                return

            duration = float(info['format'].get('duration', 0))
            
            if media_type in ['audio', 'voice'] and duration > MAX_AUDIO_DURATION:
                await bot_instance.send_message(chat_id, f"Слишком длинный аудиофайл ({duration:.1f}с > {MAX_AUDIO_DURATION}с).")
                return
            elif media_type in ['video', 'animation', 'video_note', 'sticker_video'] and duration > MAX_VIDEO_DURATION:
                await bot_instance.send_message(chat_id, f"Слишком длинное видео ({duration:.1f}с > {MAX_VIDEO_DURATION}с).")
                return

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
            output_path = [jg:пароль_(regexp)_210]ut.mp3"
            success = await apply_ffmpeg_audio_distortion(input_path, output_path, intensity)
            final_media_type = media_type

        elif media_type in ['video', 'animation', 'video_note', 'sticker_video']:
            # Определяем расширение выходного файла
            if media_type == 'sticker_video':
                output_path = f"{input_path}_out.webm"
            else:
                output_path = [jg:пароль_(regexp)_211]ut.mp4"
            
            success = await distort_video_with_audio(input_path, output_path, intensity, media_type, update_progress)
            final_media_type = media_type

        if success and output_path and [jg:авторизационный_токен_212](output_path):
            file_to_send = FSInputFile(output_path)
            
            try:
                if progress_message:
                    await bot_instance.delete_message(chat_id, progress_message.message_id)
            except:
                pass
            
            if final_media_type == 'photo': 
                await bot_instance.send_photo(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'audio': 
                await bot_instance.send_audio(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'voice': 
                await bot_instance.send_voice(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'video':
                await bot_instance.send_video(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'animation':
                await bot_instance.send_animation(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'video_note':
                await bot_instance.send_video_note(chat_id, file_to_send)
            elif final_media_type == 'sticker_video':
                await bot_instance.send_document(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
        else:
            await bot_instance.send_message(chat_id, "Что-то пошло не так во время искажения.")

    except Exception as e:
        logging.error(f"Ошибка в воркере: {e}", exc_info=True)
        try: 
            await bot_instance.send_message(chat_id, "Произошла внутренняя ошибка при обработке.")
        except Exception as send_e: 
            logging.error(f"Не удалось отправить сообщение об ошибке: {send_e}")
    finally:
        # Очистка временных файлов
        if input_path and [jg:авторизационный_токен_213](input_path).startswith("temp_worker_"):
            shutil.rmtree([jg:авторизационный_токен_214](input_path), ignore_errors=True)
        if output_path and [jg:авторизационный_токен_215](output_path):
            try:
                os.remove(output_path)
            except:
                pass
        await [jg:авторизационный_токен_216]()

def distortion_worker_proc(bot_token: str, chat_id: int, media_info: dict, intensity: int):
    """Точка входа для нового процесса. Запускает асинхронный воркер."""
    logging.info(f"Запущен новый процесс для искажения (PID: {os.getpid()})")
    asyncio.run(distortion_worker_async(bot_token, chat_id, media_info, intensity))

# --- Фильтр и основной обработчик ---

def is_distortion_command(message: types.Message) -> bool:
    try:
        from config import BLOCKED_USERS
        if [jg:авторизационный_токен_217] in BLOCKED_USERS: return False
        text_to_check = message.caption or message.text
        if text_to_check and "дисторшн" in text_to_check.lower():
            target = message.reply_to_message or message
            return bool(target.photo or target.sticker or target.audio or target.voice or 
                       target.text or target.video or target.animation or target.video_note)
        return False
    except Exception: return False

async def handle_distortion_request(message: types.Message):
    """Основной обработчик. Скачивает файл и запускает искажение в отдельном процессе."""
    try:
        target_message = message.reply_to_message or message
        text_for_parsing = message.text if message.text else message.caption
        intensity = parse_intensity_from_text(text_for_parsing)
        
        media_info = {}
        file_to_download = None
        
        # Проверка размера файла
        file_size = 0
        if target_message.photo: 
            media_info = {'media_type': 'photo', 'ext': '.jpg'}
            file_to_download = [jg:пароль_(regexp)_218]hoto[-1]
            file_size = file_to_download.file_size or 0
        elif target_message.sticker:
            # Исправленная проверка видеостикеров
            if target_message.sticker.is_video:
                media_info = {'media_type': 'sticker_video', 'ext': '.webm'}
                file_to_download = target_message.sticker
                file_size = file_to_download.file_size or 0
            elif target_message.sticker.is_animated:
                await message.answer("Извини, анимированные стикеры (TGS) пока не поддерживаются.")
                return
            else: 
                media_info = {'media_type': 'sticker_static', 'ext': '.webp'}
                file_to_download = target_message.sticker
                file_size = file_to_download.file_size or 0
        elif target_message.audio: 
            media_info = {'media_type': 'audio', 'ext': '.mp3'}
            file_to_download = target_message.audio
            file_size = file_to_download.file_size or 0
        elif target_message.voice: 
            media_info = {'media_type': 'voice', 'ext': '.ogg'}
            file_to_download = target_message.voice
            file_size = file_to_download.file_size or 0
        elif target_message.video:
            media_info = {'media_type': 'video', 'ext': '.mp4'}
            file_to_download = target_message.video
            file_size = file_to_download.file_size or 0
        elif target_message.animation:
            media_info = {'media_type': 'animation', 'ext': '.mp4'}
            file_to_download = target_message.animation
            file_size = file_to_download.file_size or 0
        elif target_message.video_note:
            media_info = {'media_type': 'video_note', 'ext': '.mp4'}
            file_to_download = target_message.video_note
            file_size = file_to_download.file_size or 0
        elif target_message.text: 
            media_info = {'media_type': 'text', 'text': target_message.text}

        if not media_info:
            await message.answer("Не нашел, что искажать.")
            return

        # Проверка размера файла
        if file_size > MAX_FILE_SIZE:
            await message.answer(f"Файл слишком большой ({file_size/1024/1024:.1f}MB > [jg:пароль_(regexp)_219]024/1024:.1f}MB)")
            return

        if file_to_download:
            temp_dir = f"temp_worker_{random.randint(1000, 9999)}"
            os.makedirs(temp_dir, exist_ok=True)
            local_path = [jg:авторизационный_токен_220](temp_dir, f"input{media_info['ext']}")
            
            if not await download_file(file_to_download.file_id, local_path):
                await message.answer("Не смог скачать файл.")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return
            media_info['local_path'] = local_path

        await message.answer("🌀 ща, сука, искажу...")
        
        try:
            multiprocessing.set_start_method("spawn", force=True)
        except RuntimeError:
            pass
            
        proc = multiprocessing.Process(
            target=distortion_worker_proc, 
            args=(main_bot_instance.token, [jg:авторизационный_токен_221], media_info, intensity)
        )
        proc.start()

    except Exception as e:
        logging.error(f"Ошибка в handle_distortion_request: {e}", exc_info=True)
        await message.answer("Не удалось запустить обработку.")