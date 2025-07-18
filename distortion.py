import os
import asyncio
import json
import random
import logging
import re
import subprocess
import shutil
import numpy as np
from aiogram import types
from aiogram.types import FSInputFile
from PIL import Image

# Попробуем импортировать seam_carving
try:
    import seam_carving
    SEAM_CARVING_AVAILABLE = True
except ImportError:
    logging.warning("Модуль 'seam_carving' не найден. Функции seam carving будут недоступны.")
    SEAM_CARVING_AVAILABLE = False

# Импортируем общие функции
from config import bot
from whatisthere import download_file

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Вспомогательные функции ---

def map_intensity(intensity: int, out_min: float, out_max: float) -> float:
    return out_min + (intensity / 100.0) * (out_max - out_min)

def parse_intensity_from_text(text: str | None) -> int:
    if not text:
        return 25
    match = re.search(r'\b(\d+)\b', text)
    if match:
        return max(0, min(100, int(match.group(1))))
    return 25

async def run_ffmpeg_command(command: list[str]) -> tuple[bool, str]:
    logging.info(f"Запуск FFmpeg: {' '.join(command)}")
    process = await asyncio.create_subprocess_exec(
        *command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        error_message = stderr.decode(errors='ignore').strip()
        logging.error(f"FFmpeg ошибка: {error_message}")
        return False, f"Ошибка FFmpeg: {error_message}"
    return True, "Success"

# --- Функции искажения изображений (для кадров) ---

async def distort_single_image(input_path: str, output_path: str, intensity: int):
    if SEAM_CARVING_AVAILABLE:
        return await apply_seam_carving_distortion(input_path, output_path, intensity)
    return await apply_ffmpeg_image_distortion(input_path, output_path, intensity)

def _seam_carving_blocking_task(src_np, original_w, original_h, new_w, new_h, out_path):
    """Синхронная, блокирующая функция для выполнения в отдельном потоке."""
    dst = seam_carving.resize(src_np, (new_w, new_h), energy_mode='backward', order='width-first')
    Image.fromarray(dst).resize((original_w, original_h), Image.LANCZOS).save(out_path, "PNG")

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
        await loop.run_in_executor(
            None, _seam_carving_blocking_task, src, original_width, original_height, new_width, new_height, output_path
        )
        return True
    except Exception as e:
        logging.error(f"Ошибка seam carving: {e}", exc_info=True)
        return False

async def apply_ffmpeg_image_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    scale = map_intensity(intensity, 1.0, 0.2)
    hue = map_intensity(intensity, 0, 180)
    sat = map_intensity(intensity, 1.0, 3.0)
    brightness = map_intensity(intensity, 0.0, 0.3)
    vf = f"scale='iw*{scale}':'ih*{scale}',scale=iw:ih:flags=neighbor,hue=h={hue}:s={sat},eq=brightness={brightness}:saturation={sat}"
    cmd = ['ffmpeg', '-i', input_path, '-vf', vf, '-y', output_path]
    success, _ = await run_ffmpeg_command(cmd)
    return success

# --- Покадровая обработка видео ---

async def process_video_frame_by_frame(input_path: str, output_path: str, intensity: int, is_sticker: bool) -> bool:
    media_info = await get_media_info(input_path)
    frame_rate = "25"
    if media_info:
        for stream in media_info.get('streams', []):
            if stream.get('codec_type') == 'video' and stream.get('avg_frame_rate') != "0/0":
                frame_rate = stream['avg_frame_rate']
                break
    
    base_id = f"distort_{random.randint(1000, 9999)}"
    frames_dir = f"temp_{base_id}_frames"
    distorted_frames_dir = f"temp_{base_id}_distorted"
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(distorted_frames_dir, exist_ok=True)

    try:
        # 1. Извлечение кадров
        extract_cmd = ['ffmpeg']
        # ИСПРАВЛЕНО: Добавляем кодек для анимированных стикеров
        if is_sticker:
            extract_cmd.extend(['-vcodec', 'libvpx-vp9'])
        extract_cmd.extend(['-i', input_path, os.path.join(frames_dir, 'f-%04d.png')])
        
        success, msg = await run_ffmpeg_command(extract_cmd)
        if not success:
            logging.error(f"Ошибка извлечения кадров: {msg}")
            return False

        # 2. Параллельное искажение
        tasks = [
            distort_single_image(os.path.join(frames_dir, f), os.path.join(distorted_frames_dir, f), intensity)
            for f in sorted(os.listdir(frames_dir))
        ]
        await asyncio.gather(*tasks)

        # 3. Сборка кадров
        input_pattern = os.path.join(distorted_frames_dir, 'f-%04d.png')
        if is_sticker:
            collect_cmd = ['ffmpeg', '-r', frame_rate, '-i', input_pattern, '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p', '-b:v', '256k', '-an', '-y', output_path]
        else:
            collect_cmd = ['ffmpeg', '-r', frame_rate, '-i', input_pattern, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-an', '-y', output_path]
        
        success, msg = await run_ffmpeg_command(collect_cmd)
        if not success:
            logging.error(f"Ошибка сборки кадров: {msg}")
            return False
        return True
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)
        shutil.rmtree(distorted_frames_dir, ignore_errors=True)

async def apply_ffmpeg_audio_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    rate = map_intensity(intensity, 1.0, 0.5)
    crush = map_intensity(intensity, 0.0, 0.7)
    decay = map_intensity(intensity, 0.0, 0.5)
    delay = map_intensity(intensity, 20, 800)
    filters = [f"asetrate=44100*{rate},atempo=1/{rate}", f"acrusher=bits=8:mix={crush}"]
    if intensity > 40: filters.append(f"aecho=0.8:0.9:{delay}:{decay}")
    if intensity > 70: filters.append("flanger")
    cmd = ['ffmpeg', '-i', input_path, '-af', ",".join(filters), '-c:a', 'aac', '-b:a', '128k', '-y', output_path]
    success, _ = await run_ffmpeg_command(cmd)
    return success

# --- Фоновая задача и основной обработчик ---

async def run_distortion_in_background(message: types.Message, intensity: int):
    """
    Эта функция выполняется в фоне, чтобы не блокировать бота.
    Она делает всю тяжелую работу и отправляет результат.
    """
    target_message = message.reply_to_message or message
    media_type, file_id, original_extension, is_animated_sticker = None, None, "", False

    if target_message.sticker:
        if target_message.sticker.is_animated or target_message.sticker.is_video:
            media_type, file_id, ext, is_animated_sticker = 'sticker_animated', target_message.sticker.file_id, ".webm", True
        else:
            media_type, file_id, ext = 'sticker_static', target_message.sticker.file_id, ".webp"
    elif target_message.photo:
        media_type, file_id, ext = 'photo', target_message.photo[-1].file_id, ".jpg"
    elif target_message.video:
        media_type, file_id, ext = 'video', target_message.video.file_id, ".mp4"
    elif target_message.animation:
        media_type, file_id, ext = 'animation', target_message.animation.file_id, ".mp4"
    
    if not file_id: return

    input_path = f"temp_distort_in_{file_id}{ext}"
    if not await download_file(file_id, input_path):
        await message.answer("Не смог скачать файл.")
        return

    output_path, success, final_media_type = None, False, None
    temp_files = [input_path]
    
    try:
        if media_type in ['photo', 'sticker_static']:
            output_path = f"temp_out_{file_id}.jpg"
            temp_files.append(output_path)
            success = await distort_single_image(input_path, output_path, intensity)
            if success: final_media_type = 'photo'

        elif media_type in ['video', 'animation', 'sticker_animated']:
            video_ext = ".webm" if is_animated_sticker else ".mp4"
            output_path_video = f"temp_vid_{file_id}{video_ext}"
            temp_files.append(output_path_video)
            
            success = await process_video_frame_by_frame(input_path, output_path_video, intensity, is_animated_sticker)
            
            if success and not is_animated_sticker:
                output_path_audio = f"temp_aud_{file_id}.aac"
                output_path_final = f"temp_final_{file_id}.mp4"
                temp_files.extend([output_path_audio, output_path_final])
                if await apply_ffmpeg_audio_distortion(input_path, output_path_audio, intensity):
                    await run_ffmpeg_command(['ffmpeg', '-i', output_path_video, '-i', output_path_audio, '-c', 'copy', '-y', output_path_final])
                    output_path = output_path_final
                else:
                    output_path = output_path_video
            elif success:
                output_path = output_path_video
            
            final_media_type = 'sticker' if is_animated_sticker else 'video'

        # Отправка результата
        if success and output_path and os.path.exists(output_path):
            file_to_send = FSInputFile(output_path)
            if final_media_type == 'photo':
                await message.answer_photo(file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'video':
                await message.answer_video(file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'sticker':
                await message.answer_sticker(file_to_send)
        else:
            await message.answer("Что-то пошло не так во время искажения.")

    except Exception as e:
        logging.error(f"Критическая ошибка в фоновой задаче: {e}", exc_info=True)
        await message.answer("Произошла критическая ошибка при обработке.")
    finally:
        for f in temp_files:
            if os.path.exists(f): os.remove(f)
        if output_path and os.path.exists(output_path): os.remove(output_path)

# --- Фильтр и основной обработчик ---

def is_distortion_command(message: types.Message) -> bool:
    try:
        from config import BLOCKED_USERS
        if message.from_user.id in BLOCKED_USERS: return False
        text_to_check = message.caption or message.text
        if text_to_check and "дисторшн" in text_to_check.lower():
            target = message.reply_to_message or message
            return bool(target.photo or target.video or target.animation or target.sticker)
        return False
    except Exception:
        return False

async def handle_distortion_request(message: types.Message):
    """
    Основной обработчик. Запускает искажение в фоновом режиме.
    """
    try:
        text_for_parsing = message.text if message.text else message.caption
        intensity = parse_intensity_from_text(text_for_parsing)
        
        await message.answer("🌀 ща, сука...")
        
        # ИСПРАВЛЕНО: Запускаем тяжелую задачу в фоне и не ждем ее завершения
        asyncio.create_task(run_distortion_in_background(message, intensity))
        
    except Exception as e:
        logging.error(f"Ошибка в handle_distortion_request: {e}", exc_info=True)
        await message.answer("Не удалось запустить обработку.")