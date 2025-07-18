import os
import asyncio
import json
import random
import logging
import re
import subprocess
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
# Убедитесь, что эти импорты соответствуют структуре вашего проекта
from config import bot
from whatisthere import download_file # Переиспользуем функцию скачивания

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Вспомогательные функции ---

def map_intensity(intensity: int, out_min: float, out_max: float) -> float:
    """
    Линейно отображает значение интенсивности (0-100) в заданный диапазон [out_min, out_max].
    """
    return out_min + (intensity / 100.0) * (out_max - out_min)

def parse_intensity_from_text(text: str | None) -> int:
    """
    Извлекает числовое значение интенсивности из текста команды.
    Возвращает значение от 0 до 100. По умолчанию 50.
    """
    if not text:
        return 50
    
    match = re.search(r'\b(\d+)\b', text)
    if match:
        intensity = int(match.group(1))
        # Ограничиваем значение в диапазоне 0-100
        return max(0, min(100, intensity))
        
    return 50 # Значение по умолчанию

async def run_ffmpeg_command(command: list[str], input_path: str = None, output_path: str = None) -> tuple[bool, str]:
    """
    Запускает команду FFmpeg и возвращает результат.
    """
    logging.info(f"Запуск FFmpeg команды: {' '.join(command)}")
    if input_path:
        logging.info(f"Входной файл: {input_path}")
    if output_path:
        logging.info(f"Выходной файл: {output_path}")

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_message = stderr.decode(errors='ignore').strip()
            logging.error(f"FFmpeg вернул ошибку {process.returncode}: {error_message}")
            return False, f"Ошибка FFmpeg: {error_message}"
        
        logging.info(f"FFmpeg команда успешно выполнена.")
        return True, "Success"
    except FileNotFoundError:
        logging.error("FFmpeg не найден. Убедитесь, что он установлен и доступен в PATH.")
        return False, "Ошибка: FFmpeg не установлен или не найден."
    except Exception as e:
        logging.error(f"Неизвестная ошибка при запуске FFmpeg: {e}")
        return False, f"Неизвестная ошибка: {e}"

async def get_media_info(file_path: str) -> dict | None:
    """
    Получает информацию о медиафайле с помощью ffprobe.
    """
    command = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-show_format', file_path
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logging.error(f"Не удалось получить информацию о медиафайле: {stderr.decode(errors='ignore')}")
        return None
    try:
        return json.loads(stdout.decode(errors='ignore'))
    except json.JSONDecodeError as e:
        logging.error(f"Ошибка декодирования JSON из ffprobe: {e}")
        return None

# --- Функции искажения ---

async def apply_seam_carving_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    """
    Применяет дисторшн через seam carving к изображению.
    Интенсивность (0-100) напрямую определяет процент сжатия.
    """
    if not SEAM_CARVING_AVAILABLE:
        logging.error("Seam carving недоступен, пропуск.")
        return False

    try:
        # Интенсивность определяет процент сжатия. Ограничиваем до 95% во избежание ошибок.
        distort_percent = max(0, min(intensity, 95))
        
        with Image.open(input_path) as img:
            original_width, original_height = img.size
            img = img.convert("RGB")
            src = np.array(img)
            
            logging.info(f"Исходное изображение для seam carving: {original_width}x{original_height}")
            
            if original_width < 50 or original_height < 50:
                logging.warning("Изображение слишком маленькое для seam carving")
                return False
            
            new_width = int(original_width * (100 - distort_percent) / 100)
            new_height = int(original_height * (100 - distort_percent) / 100)
            
            # Предотвращаем нулевые размеры
            new_width = max(new_width, 20)
            new_height = max(new_height, 20)
            
            logging.info(f"Размеры после seam carving: {new_width}x{new_height} (сжатие {distort_percent}%)")
            
            dst = seam_carving.resize(
                src, 
                (new_width, new_height),
                energy_mode='backward',
                order='width-first',
                keep_mask=None
            )
            
            result_img = Image.fromarray(dst)
            
            # Масштабируем изображение обратно до исходного разрешения
            result_img = result_img.resize((original_width, original_height), Image.LANCZOS)
            
            result_img.save(output_path, "JPEG", quality=85)
            
            logging.info(f"Дисторшн seam carving применен успешно, сохранен в {output_path}")
            return True
            
    except Exception as e:
        logging.error(f"Ошибка при применении seam carving: {e}")
        return False

async def apply_ffmpeg_image_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    """
    Применяет фильтры FFmpeg для искажения изображения на основе интенсивности.
    """
    try:
        with Image.open(input_path) as img:
            original_width, original_height = img.size
    except Exception as e:
        logging.error(f"Не удалось получить размеры изображения для FFmpeg искажения: {e}")
        return False

    # Параметры, зависящие от интенсивности
    # Чем выше интенсивность, тем сильнее пикселизация (меньше масштаб)
    scale_factor = map_intensity(intensity, 1.0, 0.2) 
    # Сдвиг оттенка
    hue_shift = map_intensity(intensity, 0, 180) 
    # Насыщенность
    saturation = map_intensity(intensity, 1.0, 3.0) 
    # Яркость
    brightness = map_intensity(intensity, 0.0, 0.3)

    filters = [
        f"scale=iw*{scale_factor}:ih*{scale_factor},scale={original_width}:{original_height}:flags=neighbor",
        f"hue=h={hue_shift}:s={saturation}",
        "colorchannelmixer=.5:.5:.5:0:.5:.5:.5:0:.5:.5:.5:0",
        f"eq=brightness={brightness}:saturation={saturation}"
    ]

    vf_string = ",".join(filters)
    
    command = [
        'ffmpeg', '-i', input_path, '-vf', vf_string,
        '-q:v', '2', '-y', output_path
    ]
    
    success, message = await run_ffmpeg_command(command, input_path, output_path)
    if not success:
        logging.error(f"Ошибка при применении FFmpeg фильтров к изображению: {message}")
    return success

async def apply_ffmpeg_video_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    """
    Применяет фильтры FFmpeg для искажения видеодорожки на основе интенсивности.
    """
    media_info = await get_media_info(input_path)
    if not media_info:
        return False

    original_width, original_height = None, None
    for stream in media_info.get('streams', []):
        if stream.get('codec_type') == 'video':
            original_width = stream.get('width')
            original_height = stream.get('height')
            break
    
    if not original_width or not original_height:
        logging.error("Не удалось получить размеры видео для FFmpeg искажения.")
        return False

    # Параметры, зависящие от интенсивности
    speed_factor = map_intensity(intensity, 1.0, 0.3) # Замедление
    scale_factor = map_intensity(intensity, 1.0, 0.3) # Пикселизация
    hue_shift = map_intensity(intensity, 0, 180)
    saturation = map_intensity(intensity, 1.0, 2.5)
    brightness = map_intensity(intensity, 0.0, 0.2)

    filters = [
        f"setpts={speed_factor}*PTS",
        f"scale=iw*{scale_factor}:ih*{scale_factor},scale={original_width}:{original_height}:flags=neighbor",
        f"hue=h={hue_shift}:s={saturation}",
        "colorchannelmixer=.5:.5:.5:0:.5:.5:.5:0:.5:.5:.5:0",
        f"eq=brightness={brightness}:saturation={saturation}"
    ]

    vf_string = ",".join(filters)
    
    command = [
        'ffmpeg', '-i', input_path, '-vf', vf_string,
        '-c:v', 'libx264', '-crf', '28', '-preset', 'fast', '-y',
        '-an', # Удаляем аудио дорожку, она будет обработана отдельно
        output_path
    ]
    
    success, message = await run_ffmpeg_command(command, input_path, output_path)
    if not success:
        logging.error(f"Ошибка при применении FFmpeg фильтров к видео: {message}")
    return success

async def apply_ffmpeg_audio_distortion(input_path: str, output_path: str, intensity: int) -> bool:
    """
    Применяет фильтры FFmpeg для искажения аудиодорожки на основе интенсивности.
    """
    # Параметры, зависящие от интенсивности
    rate_factor = map_intensity(intensity, 1.0, 0.5) # Понижение тона
    crusher_mix = map_intensity(intensity, 0.0, 0.7)
    echo_decay = map_intensity(intensity, 0.0, 0.5)
    echo_delay = map_intensity(intensity, 20, 800)
    
    filters = [
        f"asetrate=44100*{rate_factor},atempo=1/{rate_factor}",
        f"acrusher=bits=8:mix={crusher_mix}"
    ]
    
    # Добавляем эхо и фленджер при высокой интенсивности
    if intensity > 40:
        filters.append(f"aecho=0.8:0.9:{echo_delay}:{echo_decay}")
    if intensity > 70:
        filters.append("flanger")

    af_string = ",".join(filters)
    
    command = [
        'ffmpeg', '-i', input_path, '-af', af_string,
        '-c:a', 'aac', '-b:a', '128k', '-y', output_path
    ]
    
    success, message = await run_ffmpeg_command(command, input_path, output_path)
    if not success:
        logging.error(f"Ошибка при применении FFmpeg фильтров к аудио: {message}")
    return success

async def extract_frame_and_distort(input_path: str, output_path: str, intensity: int) -> bool:
    """
    Извлекает кадр из видео и применяет к нему искажение с заданной интенсивностью.
    """
    try:
        frame_path = f"temp_frame_{random.randint(1000, 9999)}.jpg"
        
        extract_command = [
            'ffmpeg', '-i', input_path, '-ss', '1.0', # Время извлечения кадра
            '-vframes', '1', '-y', frame_path
        ]
        
        success, message = await run_ffmpeg_command(extract_command, input_path=input_path, output_path=frame_path)
        if not success:
            logging.error(f"Не удалось извлечь кадр: {message}")
            return False
        
        # Применяем искажение к кадру
        if SEAM_CARVING_AVAILABLE:
            success = await apply_seam_carving_distortion(frame_path, output_path, intensity)
        else:
            success = await apply_ffmpeg_image_distortion(frame_path, output_path, intensity)
        
        if os.path.exists(frame_path):
            os.remove(frame_path)
        
        return success
        
    except Exception as e:
        logging.error(f"Ошибка при извлечении кадра и искажении: {e}")
        return False

# --- Основной процесс обработки ---

async def process_distortion(message: types.Message, intensity: int) -> tuple[bool, str | None, str | None]:
    """
    Обрабатывает запрос на искажение, определяет тип медиа и запускает нужную функцию с заданной интенсивностью.
    Возвращает: (успех, путь к файлу, тип медиа)
    """
    target_message = message.reply_to_message if message.reply_to_message else message
    media_type = None
    file_id = None
    original_extension = ""
    
    logging.info(f"Анализируем сообщение на наличие медиа...")
    
    if target_message.photo:
        media_type = 'photo'
        file_id = target_message.photo[-1].file_id
        original_extension = ".jpg"
    elif target_message.video:
        media_type = 'video'
        file_id = target_message.video.file_id
        original_extension = ".mp4"
    elif target_message.animation:
        media_type = 'animation'
        file_id = target_message.animation.file_id
        original_extension = ".mp4"
    elif target_message.sticker:
        if target_message.sticker.is_animated or target_message.sticker.is_video:
            return False, "Извини, анимированные стикеры и видео-стикеры я искажать не умею.", None
        media_type = 'sticker'
        file_id = target_message.sticker.file_id
        original_extension = ".webp"
    
    if not file_id:
        logging.warning("Медиа не найдено")
        return False, "Не нашел, что искажать. Ответь на медиафайл или отправь его с подписью.", None

    input_path = f"temp_distort_in_{file_id}{original_extension}"
    
    if not await download_file(file_id, input_path):
        return False, "Не смог скачать файл для искажения.", None

    success = False
    output_path = None
    temp_files = [input_path]
    try:
        if media_type in ['photo', 'sticker']:
            output_path = f"temp_distort_out_{file_id}.jpg"
            temp_files.append(output_path)
            
            if SEAM_CARVING_AVAILABLE:
                success = await apply_seam_carving_distortion(input_path, output_path, intensity)
            else:
                success = await apply_ffmpeg_image_distortion(input_path, output_path, intensity)
            
            if success and media_type == 'sticker':
                media_type = 'photo' # Отправляем как фото
                
        elif media_type in ['video', 'animation']:
            output_path_video = f"temp_distort_out_video_{file_id}.mp4"
            output_path_audio = f"temp_distort_out_audio_{file_id}.aac"
            final_output_path = f"temp_distort_out_final_{file_id}.mp4"
            temp_files.extend([output_path_video, output_path_audio, final_output_path])

            video_success = await apply_ffmpeg_video_distortion(input_path, output_path_video, intensity)
            audio_success = await apply_ffmpeg_audio_distortion(input_path, output_path_audio, intensity)

            if video_success and audio_success:
                command = [
                    'ffmpeg', '-i', output_path_video, '-i', output_path_audio,
                    '-c:v', 'copy', '-c:a', 'copy', '-y', final_output_path
                ]
                success, msg = await run_ffmpeg_command(command, output_path=final_output_path)
                if success:
                    output_path = final_output_path
                else:
                    logging.error(f"Не удалось объединить видео и аудио: {msg}")
            elif video_success:
                output_path = output_path_video
                success = True
            
            # Если не получилось исказить видео, извлекаем кадр
            if not success:
                logging.warning("Искажение видео не удалось, извлекаем кадр.")
                output_path = f"temp_distort_out_frame_{file_id}.jpg"
                temp_files.append(output_path)
                success = await extract_frame_and_distort(input_path, output_path, intensity)
                if success:
                    media_type = 'photo'
    
    finally:
        # Очистка всех временных файлов
        for f in temp_files:
            if os.path.exists(f):
                os.remove(f)

    if success and output_path and os.path.exists(output_path):
        # Переименовываем финальный файл, чтобы он не удалился до отправки
        final_result_path = f"final_{os.path.basename(output_path)}"
        os.rename(output_path, final_result_path)
        return True, final_result_path, media_type
    else:
        return False, "Что-то пошло не так во время искажения. Попробуй еще раз.", None

# --- Фильтр и обработчик команды ---

def is_distortion_command(message: types.Message) -> bool:
    """
    Проверяет, является ли сообщение командой дисторшн.
    """
    try:
        from config import BLOCKED_USERS
        if message.from_user.id in BLOCKED_USERS:
            return False
        
        text_to_check = message.caption or message.text
        if text_to_check and "дисторшн" in text_to_check.lower():
            if message.reply_to_message:
                reply_msg = message.reply_to_message
                return bool(reply_msg.photo or reply_msg.video or reply_msg.animation or reply_msg.sticker)
            return bool(message.photo or message.video or message.animation or message.sticker)
            
        return False
    except Exception as e:
        logging.error(f"Ошибка в фильтре is_distortion_command: {e}")
        return False

async def handle_distortion_request(message: types.Message):
    """
    Основной обработчик команды дисторшн.
    """
    try:
        logging.info(f"Получен запрос на дисторшн от пользователя {message.from_user.id}")
        
        # Определяем текст для парсинга интенсивности
        text_for_parsing = message.text if message.text else message.caption
        intensity = parse_intensity_from_text(text_for_parsing)
        
        await message.answer(f"🌀 ща, сука... (интенсивность: {intensity})")
        
        logging.info(f"Начинаем обработку дисторшна с интенсивностью {intensity}")
        success, result_path_or_error, media_type = await process_distortion(message, intensity)
        
        if not success:
            logging.error(f"Ошибка при обработке дисторшна: {result_path_or_error}")
            await message.answer(result_path_or_error)
            return
        
        file_path = result_path_or_error
        logging.info(f"Дисторшн готов, отправляем файл: {file_path}, тип: {media_type}")
        
        try:
            file_to_send = FSInputFile(file_path)
            
            if media_type == 'photo':
                await message.answer_photo(file_to_send, caption="🌀 твоя хуйня готова")
            elif media_type in ['video', 'animation']:
                await message.answer_video(file_to_send, caption="🌀 твоя хуйня готова")
            
        except Exception as e:
            logging.error(f"Ошибка при отправке искаженного файла: {e}")
            await message.answer("Искажение готово, но не смог отправить файл. Попробуй еще раз.")
        
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)
            
    except Exception as e:
        logging.error(f"Ошибка в handle_distortion_request: {e}", exc_info=True)
        await message.answer("Произошла критическая ошибка при обработке запроса. Попробуй еще раз.")
