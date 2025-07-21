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

# Импортируем общие функции
from config import bot
from whatisthere import download_file

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- ОГРАНИЧЕНИЯ РЕСУРСОВ ---
MAX_VIDEO_DURATION = 15
MAX_STICKER_DURATION = 3
MAX_AUDIO_DURATION = 180 # 3 минуты
PREPROCESS_RESOLUTION = 480

# --- Вспомогательные функции ---

def map_intensity(intensity: int, out_min: float, out_max: float) -> float:
    return out_min + (intensity / 100.0) * (out_max - out_min)

def parse_intensity_from_text(text: str | None) -> int:
    if not text: return 25
    match = re.search(r'\b(\d+)\b', text)
    if match: return max(0, min(100, int(match.group(1))))
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
        return False, error_message
    return True, "Success"

async def get_media_info(file_path: str, is_sticker: bool) -> dict | None:
    command = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-show_format']
    if is_sticker:
        command.extend(['-vcodec', 'libvpx-vp9'])
    command.extend(['-i', file_path])
    
    process = await asyncio.create_subprocess_exec(*command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = await process.communicate()
    if process.returncode != 0: return None
    try: return json.loads(stdout.decode(errors='ignore'))
    except json.JSONDecodeError: return None

# --- НОВЫЕ ФУНКЦИИ ИСКАЖЕНИЯ ---

def distort_text(text: str, intensity: int) -> str:
    """Искажает текст с заданной интенсивностью."""
    chars = list(text)
    num_changes = int(len(chars) * (intensity / 100.0))

    for _ in range(num_changes):
        idx = random.randint(0, len(chars) - 1)
        char = chars[idx]
        
        # Пропускаем пробелы
        if char.isspace():
            continue

        action = random.randint(0, 4)
        if action == 0 and intensity > 20: # Смена регистра
            chars[idx] = char.swapcase()
        elif action == 1 and intensity > 40: # Добавление диакритики (Zalgo)
            combining_chars = [chr(c) for c in range(0x0300, 0x036F)]
            chars[idx] += random.choice(combining_chars)
        elif action == 2 and intensity > 60: # Замена на похожий символ
            replacements = {'о': 'o', 'е': 'e', 'а': 'a', 'р': 'p', 'с': 'c'}
            if char.lower() in replacements:
                chars[idx] = replacements[char.lower()]
        elif action == 3 and intensity > 80: # Вставка случайного символа
            chars.insert(idx, random.choice("!@#$%^&*()"))
        else: # Небольшое изменение регистра
             if random.random() > 0.5:
                 chars[idx] = char.upper()
             else:
                 chars[idx] = char.lower()

    return "".join(chars)

async def apply_ffmpeg_audio_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    rate = map_intensity(intensity, 1.0, 0.5)
    crush = map_intensity(intensity, 0.0, 0.7)
    decay = map_intensity(intensity, 0.0, 0.5)
    delay = map_intensity(intensity, 20, 800)
    filters = [f"asetrate=44100*{rate},atempo=1/{rate}", f"acrusher=bits=8:mix={crush}"]
    if intensity > 40: filters.append(f"aecho=0.8:0.9:{delay}:{decay}")
    if intensity > 70: filters.append("flanger")
    cmd = ['ffmpeg', '-i', input_path, '-af', ",".join(filters), '-c:a', 'libmp3lame', '-q:a', '5', '-y', output_path]
    success, _ = await run_ffmpeg_command(cmd)
    return success

# --- Функции искажения изображений (для кадров) ---

async def distort_single_image(input_path: str, output_path: str, intensity: int):
    # ... (код без изменений)
    if SEAM_CARVING_AVAILABLE:
        return await apply_seam_carving_distortion(input_path, output_path, intensity)
    return await apply_ffmpeg_image_distortion(input_path, output_path, intensity)

def _seam_carving_blocking_task(src_np, original_w, original_h, new_w, new_h, out_path):
    # ... (код без изменений)
    dst = seam_carving.resize(src_np, (new_w, new_h), energy_mode='backward', order='width-first')
    Image.fromarray(dst).resize((original_w, original_h), Image.LANCZOS).save(out_path, "PNG")

async def apply_seam_carving_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    # ... (код без изменений)
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

async def apply_ffmpeg_image_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    # ... (код без изменений)
    scale = map_intensity(intensity, 1.0, 0.2)
    hue = map_intensity(intensity, 0, 180)
    sat = map_intensity(intensity, 1.0, 3.0)
    brightness = map_intensity(intensity, 0.0, 0.3)
    vf = f"scale='iw*{scale}':'ih*{scale}',scale=iw:ih:flags=neighbor,hue=h={hue}:s={sat},eq=brightness={brightness}:saturation={sat}"
    cmd = ['ffmpeg', '-i', input_path, '-vf', vf, '-y', output_path]
    success, _ = await run_ffmpeg_command(cmd)
    return success

# --- Покадровая обработка видео ---

async def process_video_frame_by_frame(input_path: str, output_path: str, intensity: int, is_sticker: bool, frame_rate: str) -> bool:
    # ... (код без изменений)
    base_id = f"distort_{random.randint(1000, 9999)}"
    frames_dir = f"temp_{base_id}_frames"
    distorted_frames_dir = f"temp_{base_id}_distorted"
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(distorted_frames_dir, exist_ok=True)

    try:
        extract_cmd = ['ffmpeg']
        if is_sticker:
            extract_cmd.extend(['-vcodec', 'libvpx-vp9'])
        extract_cmd.extend(['-i', input_path, os.path.join(frames_dir, 'f-%04d.png')])
        
        success, msg = await run_ffmpeg_command(extract_cmd)
        if not success:
            logging.error(f"Ошибка извлечения кадров: {msg}")
            return False

        tasks = [distort_single_image(os.path.join(frames_dir, f), os.path.join(distorted_frames_dir, f), intensity) for f in sorted(os.listdir(frames_dir))]
        await asyncio.gather(*tasks)

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

# --- ИЗОЛИРОВАННЫЙ ПРОЦЕСС ОБРАБОТКИ ---

async def distortion_worker_async(bot_token: str, chat_id: int, media_info: dict, intensity: int):
    """Асинхронная часть воркера, которая выполняет всю работу."""
    bot_instance = Bot(token=bot_token)
    
    file_id = media_info['file_id']
    media_type = media_info['media_type']
    ext = media_info['ext']
    is_animated_sticker = media_type == 'sticker_animated'
    
    input_path = f"temp_distort_in_{file_id}{ext}"
    if not await download_file(file_id, input_path, bot_instance):
        await bot_instance.send_message(chat_id, "Не смог скачать файл.")
        return

    temp_files = [input_path]
    output_path = None
    
    try:
        # --- БЛОК ПРОВЕРКИ И ПРЕПРОЦЕССИНГА ---
        if media_type in ['video', 'animation', 'sticker_animated', 'audio', 'voice']:
            info = await get_media_info(input_path, is_animated_sticker)
            if not info or 'format' not in info or 'duration' not in info['format']:
                await bot_instance.send_message(chat_id, "Не удалось прочитать информацию о файле. Возможно, он поврежден.")
                raise Exception("Failed to get media info")

            duration = float(info['format']['duration'])
            limit = MAX_STICKER_DURATION if is_animated_sticker else (MAX_AUDIO_DURATION if media_type in ['audio', 'voice'] else MAX_VIDEO_DURATION)
            if duration > limit:
                await bot_instance.send_message(chat_id, f"Слишком длинный файл ({duration:.1f}с > {limit}с). Я такое не потяну.")
                raise Exception("Duration limit exceeded")
            
            # Препроцессинг для видео/гиф
            if media_type in ['video', 'animation']:
                logging.info(f"Препроцессинг видео до {PREPROCESS_RESOLUTION}p...")
                preprocessed_path = f"{input_path}_preprocessed.mp4"
                temp_files.append(preprocessed_path)
                vf_filter = f"scale=-2:'min(ih,{PREPROCESS_RESOLUTION})'"
                cmd = ['ffmpeg', '-i', input_path, '-vf', vf_filter, '-c:v', 'libx264', '-an', '-y', preprocessed_path]
                if not (await run_ffmpeg_command(cmd))[0]:
                    await bot_instance.send_message(chat_id, "Ошибка при подготовке видео.")
                    raise Exception("Preprocessing failed")
                input_path = preprocessed_path # Дальше работаем с уменьшенной версией

        # --- ОСНОВНАЯ ОБРАБОТКА ---
        success, final_media_type = False, None
        
        if media_type == 'text':
            distorted_text = distort_text(media_info['text'], intensity)
            await bot_instance.send_message(chat_id, distorted_text)
            return # Текст обработан, выходим

        elif media_type in ['photo', 'sticker_static']:
            output_path = f"temp_out_{file_id}.jpg"
            success = await distort_single_image(input_path, output_path, intensity)
            final_media_type = 'photo'

        elif media_type in ['audio', 'voice']:
            output_path = f"temp_out_{file_id}.mp3"
            success = await apply_ffmpeg_audio_distortion(input_path, output_path, intensity)
            final_media_type = media_type

        elif media_type in ['video', 'animation', 'sticker_animated']:
            frame_rate = "25"
            for stream in info.get('streams', []):
                if stream.get('codec_type') == 'video' and stream.get('avg_frame_rate') != "0/0":
                    frame_rate = stream['avg_frame_rate']
                    break
            
            video_ext = ".webm" if is_animated_sticker else ".mp4"
            output_path_video = f"temp_vid_{file_id}{video_ext}"
            
            success = await process_video_frame_by_frame(input_path, output_path_video, intensity, is_animated_sticker, frame_rate)
            
            if success and not is_animated_sticker:
                output_path_audio = f"temp_aud_{file_id}.aac"
                output_path_final = f"temp_final_{file_id}.mp4"
                temp_files.extend([output_path_audio, output_path_final])
                if await apply_ffmpeg_audio_distortion(media_info['original_input_path'], output_path_audio, intensity):
                    await run_ffmpeg_command(['ffmpeg', '-i', output_path_video, '-i', output_path_audio, '-c', 'copy', '-y', output_path_final])
                    output_path = output_path_final
                else: output_path = output_path_video
            elif success: output_path = output_path_video
            
            final_media_type = 'sticker' if is_animated_sticker else 'video'

        # --- ОТПРАВКА РЕЗУЛЬТАТА ---
        if success and output_path and os.path.exists(output_path):
            file_to_send = FSInputFile(output_path)
            if final_media_type == 'photo': await bot_instance.send_photo(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'video': await bot_instance.send_video(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'sticker': await bot_instance.send_sticker(chat_id, file_to_send)
            elif final_media_type == 'audio': await bot_instance.send_audio(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
            elif final_media_type == 'voice': await bot_instance.send_voice(chat_id, file_to_send, caption="🌀 твоя хуйня готова")
        else:
            if 'duration' not in locals() or duration <= limit:
                 await bot_instance.send_message(chat_id, "Что-то пошло не так во время искажения.")

    except Exception as e:
        logging.error(f"Ошибка в воркере: {e}", exc_info=True)
    finally:
        temp_files.append(output_path)
        for f in temp_files:
            if f and os.path.exists(f): os.remove(f)
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
            return bool(target.photo or target.video or target.animation or target.sticker or target.audio or target.voice or target.text)
        return False
    except Exception: return False

async def handle_distortion_request(message: types.Message):
    """
    Основной обработчик. Собирает информацию и запускает искажение в отдельном процессе.
    """
    try:
        target_message = message.reply_to_message or message
        text_for_parsing = message.text if message.text else message.caption
        intensity = parse_intensity_from_text(text_for_parsing)
        
        media_info = {}
        if target_message.sticker:
            if target_message.sticker.is_animated or target_message.sticker.is_video:
                media_info = {'media_type': 'sticker_animated', 'file_id': target_message.sticker.file_id, 'ext': '.webm'}
            else: media_info = {'media_type': 'sticker_static', 'file_id': target_message.sticker.file_id, 'ext': '.webp'}
        elif target_message.photo: media_info = {'media_type': 'photo', 'file_id': target_message.photo[-1].file_id, 'ext': '.jpg'}
        elif target_message.video: media_info = {'media_type': 'video', 'file_id': target_message.video.file_id, 'ext': '.mp4'}
        elif target_message.animation: media_info = {'media_type': 'animation', 'file_id': target_message.animation.file_id, 'ext': '.mp4'}
        elif target_message.audio: media_info = {'media_type': 'audio', 'file_id': target_message.audio.file_id, 'ext': '.mp3'}
        elif target_message.voice: media_info = {'media_type': 'voice', 'file_id': target_message.voice.file_id, 'ext': '.ogg'}
        elif target_message.text: media_info = {'media_type': 'text', 'text': target_message.text}
        
        if not media_info:
            await message.answer("Не нашел, что искажать.")
            return

        await message.answer("🌀 ща, сука...")
        
        # Запускаем тяжелую задачу в отдельном процессе
        proc = multiprocessing.Process(target=distortion_worker_proc, args=(bot.token, message.chat.id, media_info, intensity))
        proc.start()

    except Exception as e:
        logging.error(f"Ошибка в handle_distortion_request: {e}", exc_info=True)
        await message.answer("Не удалось запустить обработку.")
