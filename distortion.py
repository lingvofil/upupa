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

# Попробуем импортировать seam_carving. Если не установлен, функции, использующие его, будут недоступны.
try:
    import seam_carving
    SEAM_CARVING_AVAILABLE = True
except ImportError:
    logging.warning("Модуль 'seam_carving' не найден. Функции seam carving будут недоступны.")
    SEAM_CARVING_AVAILABLE = False

# Импортируем общие функции и переменные из других модулей
from config import bot
from whatisthere import download_file

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Вспомогательные функции ---

def map_intensity(intensity: int, out_min: float, out_max: float) -> float:
    return out_min + (intensity / 100.0) * (out_max - out_min)

def parse_intensity_from_text(text: str | None) -> int:
    """
    Извлекает числовое значение интенсивности из текста команды.
    Возвращает значение от 0 до 100. По умолчанию 25.
    """
    if not text:
        return 25 # ИЗМЕНЕНО: Значение по умолчанию теперь 25
    
    match = re.search(r'\b(\d+)\b', text)
    if match:
        intensity = int(match.group(1))
        return max(0, min(100, intensity))
        
    return 25 # ИЗМЕНЕНО: Значение по умолчанию теперь 25

async def run_ffmpeg_command(command: list[str]) -> tuple[bool, str]:
    """Запускает команду FFmpeg и возвращает результат."""
    logging.info(f"Запуск FFmpeg команды: {' '.join(command)}")
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            error_message = stderr.decode(errors='ignore').strip()
            logging.error(f"FFmpeg вернул ошибку {process.returncode}: {error_message}")
            return False, f"Ошибка FFmpeg: {error_message}"
        return True, "Success"
    except FileNotFoundError:
        return False, "Ошибка: FFmpeg не установлен или не найден."
    except Exception as e:
        return False, f"Неизвестная ошибка: {e}"

async def get_media_info(file_path: str) -> dict | None:
    """Получает информацию о медиафайле с помощью ffprobe."""
    command = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-show_format', file_path]
    process = await asyncio.create_subprocess_exec(*command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        logging.error(f"Не удалось получить информацию о медиафайле: {stderr.decode(errors='ignore')}")
        return None
    try:
        return json.loads(stdout.decode(errors='ignore'))
    except json.JSONDecodeError:
        return None

# --- Функции искажения изображений (для кадров) ---

async def distort_single_image(input_path: str, output_path: str, intensity: int):
    """Выбирает и применяет метод искажения к одному изображению."""
    if SEAM_CARVING_AVAILABLE:
        return await apply_seam_carving_distortion(input_path, output_path, intensity)
    return await apply_ffmpeg_image_distortion(input_path, output_path, intensity)

async def apply_seam_carving_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    """Применяет seam carving к изображению, запуская ресурсоемкую часть в отдельном потоке."""
    if not SEAM_CARVING_AVAILABLE: return False

    def blocking_task(src_np, original_w, original_h, new_w, new_h, out_path):
        dst = seam_carving.resize(src_np, (new_w, new_h), energy_mode='backward', order='width-first')
        Image.fromarray(dst).resize((original_w, original_h), Image.LANCZOS).save(out_path, "PNG")

    try:
        distort_percent = max(0, min(intensity, 95))
        with Image.open(input_path) as img:
            if img.width < 50 or img.height < 50: return False
            original_width, original_height = img.size
            img = img.convert("RGB")
            src = np.array(img)
        
        new_width = max(int(original_width * (100 - distort_percent) / 100), 20)
        new_height = max(int(original_height * (100 - distort_percent) / 100), 20)
        
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, blocking_task, src, original_width, original_height, new_width, new_height, output_path)
        return True
    except Exception as e:
        logging.error(f"Ошибка при применении seam carving: {e}", exc_info=True)
        return False

async def apply_ffmpeg_image_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    """Применяет фильтры FFmpeg к одному изображению."""
    scale_factor = map_intensity(intensity, 1.0, 0.2)
    hue_shift = map_intensity(intensity, 0, 180)
    saturation = map_intensity(intensity, 1.0, 3.0)
    brightness = map_intensity(intensity, 0.0, 0.3)
    
    filters = [
        f"scale='iw*{scale_factor}':'ih*{scale_factor}',scale=iw:ih:flags=neighbor",
        f"hue=h={hue_shift}:s={saturation}",
        f"eq=brightness={brightness}:saturation={saturation}"
    ]
    vf_string = ",".join(filters)
    command = ['ffmpeg', '-i', input_path, '-vf', vf_string, '-y', output_path]
    success, _ = await run_ffmpeg_command(command)
    return success

# --- НОВАЯ ЛОГИКА: Покадровая обработка видео ---

async def process_video_frame_by_frame(input_path: str, output_path: str, intensity: int, is_sticker: bool) -> bool:
    """
    Полностью новая функция для обработки видео/анимаций/стикеров покадрово.
    """
    media_info = await get_media_info(input_path)
    if not media_info: return False

    # Получаем частоту кадров
    frame_rate = "25" # Значение по умолчанию
    for stream in media_info.get('streams', []):
        if stream.get('codec_type') == 'video' and 'avg_frame_rate' in stream:
            if stream['avg_frame_rate'] != "0/0":
                frame_rate = stream['avg_frame_rate']
                break
    
    # Создаем временные директории
    base_id = f"distort_{random.randint(1000, 9999)}"
    frames_dir = f"temp_{base_id}_frames"
    distorted_frames_dir = f"temp_{base_id}_distorted"
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(distorted_frames_dir, exist_ok=True)

    try:
        # 1. Извлечение кадров
        logging.info(f"Извлечение кадров из {input_path}...")
        extract_cmd = ['ffmpeg', '-i', input_path, os.path.join(frames_dir, 'frame-%04d.png')]
        success, msg = await run_ffmpeg_command(extract_cmd)
        if not success:
            logging.error(f"Ошибка извлечения кадров: {msg}")
            return False

        # 2. Параллельное искажение каждого кадра
        tasks = []
        frame_files = sorted(os.listdir(frames_dir))
        for frame_file in frame_files:
            in_frame = os.path.join(frames_dir, frame_file)
            out_frame = os.path.join(distorted_frames_dir, frame_file)
            tasks.append(distort_single_image(in_frame, out_frame, intensity))
        
        await asyncio.gather(*tasks)
        logging.info(f"Искажено {len(frame_files)} кадров.")

        # 3. Сборка кадров обратно в видео/стикер
        logging.info("Сборка искаженных кадров...")
        input_frames_pattern = os.path.join(distorted_frames_dir, 'frame-%04d.png')
        
        if is_sticker:
            # Команда для сборки анимированного стикера .webm
            collect_cmd = [
                'ffmpeg', '-r', frame_rate, '-i', input_frames_pattern,
                '-c:v', 'libvpx-vp9', '-pix_fmt', 'yuva420p', 
                '-b:v', '256k', # Битрейт для стикеров
                '-an', '-y', output_path
            ]
        else:
            # Команда для сборки обычного видео .mp4
            collect_cmd = [
                'ffmpeg', '-r', frame_rate, '-i', input_frames_pattern,
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', 
                '-an', '-y', output_path
            ]
        
        success, msg = await run_ffmpeg_command(collect_cmd)
        if not success:
            logging.error(f"Ошибка сборки кадров: {msg}")
            return False

        return True

    finally:
        # Очистка временных директорий
        shutil.rmtree(frames_dir, ignore_errors=True)
        shutil.rmtree(distorted_frames_dir, ignore_errors=True)


# --- Основной процесс обработки ---

async def process_distortion(message: types.Message, intensity: int) -> tuple[bool, str | None, str | None]:
    """
    Основной обработчик: определяет тип медиа и вызывает соответствующую функцию.
    """
    target_message = message.reply_to_message if message.reply_to_message else message
    media_type, file_id, original_extension = None, None, ""
    is_animated_sticker = False

    if target_message.sticker:
        if target_message.sticker.is_animated or target_message.sticker.is_video:
            media_type, file_id, original_extension = 'sticker_animated', target_message.sticker.file_id, ".webm"
            is_animated_sticker = True
        else:
            media_type, file_id, original_extension = 'sticker_static', target_message.sticker.file_id, ".webp"
    elif target_message.photo:
        media_type, file_id, original_extension = 'photo', target_message.photo[-1].file_id, ".jpg"
    elif target_message.video:
        media_type, file_id, original_extension = 'video', target_message.video.file_id, ".mp4"
    elif target_message.animation:
        media_type, file_id, original_extension = 'animation', target_message.animation.file_id, ".mp4"
    
    if not file_id:
        return False, "Не нашел, что искажать.", None

    input_path = f"temp_distort_in_{file_id}{original_extension}"
    if not await download_file(file_id, input_path):
        return False, "Не смог скачать файл.", None

    output_path, success = None, False
    try:
        if media_type in ['photo', 'sticker_static']:
            output_path = f"temp_distort_out_{file_id}.jpg"
            success = await distort_single_image(input_path, output_path, intensity)
            if success: media_type = 'photo'

        elif media_type in ['video', 'animation', 'sticker_animated']:
            ext = ".webm" if is_animated_sticker else ".mp4"
            output_path_video = f"temp_distort_out_video_{file_id}{ext}"
            
            # Покадровая обработка
            success = await process_video_frame_by_frame(input_path, output_path_video, intensity, is_animated_sticker)
            
            if success:
                # Для обычного видео добавляем искаженный звук
                if not is_animated_sticker:
                    output_path_audio = f"temp_distort_out_audio_{file_id}.aac"
                    output_path_final = f"temp_distort_out_final_{file_id}.mp4"
                    
                    # Искажаем аудио
                    audio_success = await apply_ffmpeg_audio_distortion(input_path, output_path_audio, intensity)
                    if audio_success:
                        # Объединяем видео и аудио
                        merge_cmd = ['ffmpeg', '-i', output_path_video, '-i', output_path_audio, '-c', 'copy', '-y', output_path_final]
                        await run_ffmpeg_command(merge_cmd)
                        output_path = output_path_final
                        os.remove(output_path_video)
                        os.remove(output_path_audio)
                    else:
                        output_path = output_path_video # Если аудио не удалось, отправляем без звука
                else:
                    output_path = output_path_video # Для стикеров звук не нужен
            
            if success and is_animated_sticker:
                 media_type = 'sticker' # Тип для отправки в Telegram
            else:
                 media_type = 'video'

    finally:
        if os.path.exists(input_path): os.remove(input_path)

    if success and output_path and os.path.exists(output_path):
        return True, output_path, media_type
    else:
        if output_path and os.path.exists(output_path): os.remove(output_path)
        return False, "Что-то пошло не так во время искажения.", None

async def apply_ffmpeg_audio_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    rate_factor = map_intensity(intensity, 1.0, 0.5)
    crusher_mix = map_intensity(intensity, 0.0, 0.7)
    echo_decay = map_intensity(intensity, 0.0, 0.5)
    echo_delay = map_intensity(intensity, 20, 800)
    filters = [f"asetrate=44100*{rate_factor},atempo=1/{rate_factor}", f"acrusher=bits=8:mix={crusher_mix}"]
    if intensity > 40: filters.append(f"aecho=0.8:0.9:{echo_delay}:{echo_decay}")
    if intensity > 70: filters.append("flanger")
    af_string = ",".join(filters)
    command = ['ffmpeg', '-i', input_path, '-af', af_string, '-c:a', 'aac', '-b:a', '128k', '-y', output_path]
    success, _ = await run_ffmpeg_command(command)
    return success

# --- Фильтр и обработчик команды ---

def is_distortion_command(message: types.Message) -> bool:
    try:
        from config import BLOCKED_USERS
        if message.from_user.id in BLOCKED_USERS: return False
        text_to_check = message.caption or message.text
        if text_to_check and "дисторшн" in text_to_check.lower():
            target = message.reply_to_message or message
            return bool(target.photo or target.video or target.animation or target.sticker)
        return False
    except Exception as e:
        logging.error(f"Ошибка в фильтре is_distortion_command: {e}")
        return False

async def handle_distortion_request(message: types.Message):
    try:
        text_for_parsing = message.text if message.text else message.caption
        intensity = parse_intensity_from_text(text_for_parsing)
        await message.answer("🌀 ща, сука...")
        
        success, result_path, media_type = await process_distortion(message, intensity)
        
        if not success:
            await message.answer(result_path)
            return
        
        try:
            file_to_send = FSInputFile(result_path)
            if media_type == 'photo':
                await message.answer_photo(file_to_send, caption="🌀 твоя хуйня готова")
            elif media_type == 'video':
                await message.answer_video(file_to_send, caption="🌀 твоя хуйня готова")
            elif media_type == 'sticker':
                await message.answer_sticker(file_to_send)
        except Exception as e:
            logging.error(f"Ошибка при отправке искаженного файла: {e}", exc_info=True)
            await message.answer("Искажение готово, но не смог отправить файл.")
        finally:
            if os.path.exists(result_path): os.remove(result_path)
            
    except Exception as e:
        logging.error(f"Критическая ошибка в handle_distortion_request: {e}", exc_info=True)
        await message.answer("Произошла критическая ошибка.")
