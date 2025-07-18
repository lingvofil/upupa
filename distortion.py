import os
import asyncio
import json
import random
import logging
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
from config import bot
from whatisthere import download_file # Переиспользуем функцию скачивания

# ================== ОСНОВНОЙ ПАРАМЕТР ИНТЕНСИВНОСТИ ИСКАЖЕНИЯ ==================
# Меняйте это значение для управления общей интенсивностью всех искажений
# Диапазон: 0.1 (минимальное) - 2.0 (максимальное)
DISTORTION_INTENSITY = 0.05

# ================== БАЗОВЫЕ НАСТРОЙКИ (не трогать) ==================
# Базовые настройки seam carving
BASE_SEAM_CARVING_NORMAL = 60
BASE_SEAM_CARVING_DOUBLE_FIRST = 35
BASE_SEAM_CARVING_DOUBLE_SECOND = 25
BASE_SEAM_CARVING_EXTREME = 80

# Базовые настройки FFmpeg для изображений
BASE_FFMPEG_IMAGE_SCALE = 0.7
BASE_FFMPEG_IMAGE_HUE = 45
BASE_FFMPEG_IMAGE_SATURATION = 1.8

# Базовые настройки FFmpeg для видео
BASE_FFMPEG_VIDEO_SPEED = 0.8
BASE_FFMPEG_VIDEO_SCALE = 0.6
BASE_FFMPEG_VIDEO_HUE = 90
BASE_FFMPEG_VIDEO_SATURATION = 1.5

# Базовые настройки FFmpeg для аудио
BASE_FFMPEG_AUDIO_RATE = 0.75
BASE_FFMPEG_AUDIO_ECHO_DELAY = 500
BASE_FFMPEG_AUDIO_ECHO_DECAY = 0.4

# Базовые настройки извлечения кадров
BASE_FRAME_EXTRACT_TIME = 1.0

# ================== ВЫЧИСЛЯЕМЫЕ ПАРАМЕТРЫ (обновляются автоматически) ==================
def get_distortion_params():
    """
    Вычисляет параметры искажения на основе общей интенсивности.
    """
    intensity = max(0.1, min(2.0, DISTORTION_INTENSITY))  # Ограничиваем диапазон
    
    return {
        # Seam carving параметры
        'seam_normal': int(BASE_SEAM_CARVING_NORMAL * intensity),
        'seam_double_first': int(BASE_SEAM_CARVING_DOUBLE_FIRST * intensity),
        'seam_double_second': int(BASE_SEAM_CARVING_DOUBLE_SECOND * intensity),
        'seam_extreme': int(BASE_SEAM_CARVING_EXTREME * intensity),
        
        # FFmpeg изображения
        'ffmpeg_img_scale': max(0.1, BASE_FFMPEG_IMAGE_SCALE * (2 - intensity)),  # Инвертируем для масштаба
        'ffmpeg_img_hue': BASE_FFMPEG_IMAGE_HUE * intensity,
        'ffmpeg_img_saturation': BASE_FFMPEG_IMAGE_SATURATION * intensity,
        
        # FFmpeg видео
        'ffmpeg_vid_speed': max(0.1, BASE_FFMPEG_VIDEO_SPEED * (2 - intensity)),  # Инвертируем для скорости
        'ffmpeg_vid_scale': max(0.1, BASE_FFMPEG_VIDEO_SCALE * (2 - intensity)),  # Инвертируем для масштаба
        'ffmpeg_vid_hue': BASE_FFMPEG_VIDEO_HUE * intensity,
        'ffmpeg_vid_saturation': BASE_FFMPEG_VIDEO_SATURATION * intensity,
        
        # FFmpeg аудио
        'ffmpeg_audio_rate': max(0.1, BASE_FFMPEG_AUDIO_RATE * (2 - intensity)),  # Инвертируем для скорости
        'ffmpeg_audio_echo_delay': int(BASE_FFMPEG_AUDIO_ECHO_DELAY * intensity),
        'ffmpeg_audio_echo_decay': min(0.9, BASE_FFMPEG_AUDIO_ECHO_DECAY * intensity),
        
        # Кадры
        'frame_extract_time': BASE_FRAME_EXTRACT_TIME
    }

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def run_ffmpeg_command(command: list[str], input_path: str = None, output_path: str = None) -> tuple[bool, str]:
    """
    Запускает команду FFmpeg и возвращает результат.
    Args:
        command: Список аргументов команды FFmpeg.
        input_path: Путь к входному файлу (для логирования).
        output_path: Путь к выходному файлу (для логирования).
    Returns:
        tuple[bool, str]: (True, "Success") если успешно, (False, "Error message") если ошибка.
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
    # Используем stdout=subprocess.PIPE и stderr=subprocess.PIPE для захвата вывода
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

async def apply_seam_carving_distortion(input_path: str, output_path: str, distort_percent: int) -> bool:
    """
    Применяет дисторшн через seam carving к изображению, затем масштабирует обратно до исходного размера.
    
    Args:
        input_path: Путь к исходному изображению
        output_path: Путь для сохранения результата
        distort_percent: Процент сжатия (чем больше, тем больше искажение)
    
    Returns:
        bool: True если успешно, False если ошибка
    """
    if not SEAM_CARVING_AVAILABLE:
        logging.error("Seam carving недоступен, пропуск.")
        return False

    try:
        with Image.open(input_path) as img:
            original_width, original_height = img.size
            img = img.convert("RGB")
            src = np.array(img)
            
            logging.info(f"Исходное изображение для seam carving: {original_width}x{original_height}")
            
            if original_width < 50 or original_height < 50:
                logging.warning("Изображение слишком маленькое для seam carving")
                return False
            
            # Ограничиваем процент искажения
            distort_percent = min(distort_percent, 90)
            
            new_width = int(original_width * (100 - distort_percent) / 100)
            new_height = int(original_height * (100 - distort_percent) / 100)
            
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
            
            logging.info(f"Дисторшн seam carving применен успешно и масштабирован до {original_width}x{original_height}, сохранен в {output_path}")
            return True
            
    except Exception as e:
        logging.error(f"Ошибка при применении seam carving: {e}")
        return False

async def apply_normal_seam_carving(input_path: str, output_path: str) -> bool:
    """
    Применяет обычный seam carving с вычисляемым процентом искажения.
    """
    if not SEAM_CARVING_AVAILABLE:
        logging.error("Seam carving недоступен, пропуск.")
        return False

    params = get_distortion_params()
    percent = params['seam_normal']
    logging.info(f"Применяем обычный дисторшн seam carving с процентом: {percent}% (интенсивность: {DISTORTION_INTENSITY})")
    return await apply_seam_carving_distortion(input_path, output_path, percent)

async def apply_double_seam_carving(input_path: str, output_path: str) -> bool:
    """
    Применяет двойной seam carving с вычисляемыми параметрами.
    """
    if not SEAM_CARVING_AVAILABLE:
        logging.error("Seam carving недоступен, пропуск.")
        return False

    try:
        params = get_distortion_params()
        temp_path = f"temp_seam_double_{random.randint(1000, 9999)}.jpg"
        
        logging.info(f"Двойной seam carving: первый проход {params['seam_double_first']}%, второй проход {params['seam_double_second']}%")
        
        if not await apply_seam_carving_distortion(input_path, temp_path, params['seam_double_first']):
            return False
        
        success = await apply_seam_carving_distortion(temp_path, output_path, params['seam_double_second'])
        
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        return success
        
    except Exception as e:
        logging.error(f"Ошибка при двойном seam carving: {e}")
        return False

async def apply_extreme_seam_carving(input_path: str, output_path: str) -> bool:
    """
    Применяет экстремальный seam carving с вычисляемым процентом искажения.
    """
    if not SEAM_CARVING_AVAILABLE:
        logging.error("Seam carving недоступен, пропуск.")
        return False

    params = get_distortion_params()
    percent = params['seam_extreme']
    logging.info(f"Применяем экстремальный дисторшн seam carving с процентом: {percent}% (интенсивность: {DISTORTION_INTENSITY})")
    return await apply_seam_carving_distortion(input_path, output_path, percent)

async def apply_ffmpeg_image_distortion(input_path: str, output_path: str) -> bool:
    """
    Применяет вычисляемые фильтры FFmpeg для искажения изображения.
    """
    try:
        with Image.open(input_path) as img:
            original_width, original_height = img.size
    except Exception as e:
        logging.error(f"Не удалось получить размеры изображения для FFmpeg искажения: {e}")
        return False

    params = get_distortion_params()
    
    # Вычисляемые фильтры на основе интенсивности
    filters = [
        f"scale=iw*{params['ffmpeg_img_scale']}:ih*{params['ffmpeg_img_scale']},scale={original_width}:{original_height}:flags=neighbor",
        f"hue=h={params['ffmpeg_img_hue']}:s={params['ffmpeg_img_saturation']}",
        "colorchannelmixer=.5:.5:.5:0:.5:.5:.5:0:.5:.5:.5:0",
        f"eq=brightness={0.1 * DISTORTION_INTENSITY}:saturation={1.5 * DISTORTION_INTENSITY}"
    ]

    vf_string = ",".join(filters)
    
    logging.info(f"FFmpeg фильтры для изображения (интенсивность {DISTORTION_INTENSITY}): {vf_string}")
    
    command = [
        'ffmpeg',
        '-i', input_path,
        '-vf', vf_string,
        '-q:v', '2',
        '-y', output_path
    ]
    
    success, message = await run_ffmpeg_command(command, input_path, output_path)
    if not success:
        logging.error(f"Ошибка при применении FFmpeg фильтров к изображению: {message}")
    return success

async def apply_ffmpeg_video_distortion(input_path: str, output_path: str) -> bool:
    """
    Применяет вычисляемые фильтры FFmpeg для искажения видео.
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

    params = get_distortion_params()
    
    # Вычисляемые фильтры на основе интенсивности
    filters = [
        f"setpts={params['ffmpeg_vid_speed']}*PTS",
        f"scale=iw*{params['ffmpeg_vid_scale']}:ih*{params['ffmpeg_vid_scale']},scale={original_width}:{original_height}:flags=neighbor",
        f"hue=h={params['ffmpeg_vid_hue']}:s={params['ffmpeg_vid_saturation']}",
        "colorchannelmixer=.5:.5:.5:0:.5:.5:.5:0:.5:.5:.5:0",
        f"eq=brightness={0.1 * DISTORTION_INTENSITY}:saturation={1.5 * DISTORTION_INTENSITY}"
    ]

    vf_string = ",".join(filters)
    
    logging.info(f"FFmpeg фильтры для видео (интенсивность {DISTORTION_INTENSITY}): {vf_string}")
    
    command = [
        'ffmpeg',
        '-i', input_path,
        '-vf', vf_string,
        '-c:v', 'libx264',
        '-crf', '28',
        '-preset', 'fast',
        '-y',
        '-c:a', 'copy',
        output_path
    ]
    
    success, message = await run_ffmpeg_command(command, input_path, output_path)
    if not success:
        logging.error(f"Ошибка при применении FFmpeg фильтров к видео: {message}")
    return success

async def apply_ffmpeg_audio_distortion(input_path: str, output_path: str) -> bool:
    """
    Применяет вычисляемые фильтры FFmpeg для искажения аудио.
    """
    params = get_distortion_params()
    
    # Вычисляемые фильтры на основе интенсивности
    filters = [
        f"asetrate=44100*{params['ffmpeg_audio_rate']},atempo=1/{params['ffmpeg_audio_rate']}",
        f"acrusher=bits={max(4, int(8 * (2 - DISTORTION_INTENSITY)))}:mix=0.5",
        f"aecho=0.8:0.9:{params['ffmpeg_audio_echo_delay']}:{params['ffmpeg_audio_echo_decay']}",
        "flanger"
    ]

    af_string = ",".join(filters)
    
    logging.info(f"FFmpeg фильтры для аудио (интенсивность {DISTORTION_INTENSITY}): {af_string}")
    
    command = [
        'ffmpeg',
        '-i', input_path,
        '-af', af_string,
        '-c:a', 'aac',
        '-b:a', '128k',
        '-y', output_path
    ]
    
    success, message = await run_ffmpeg_command(command, input_path, output_path)
    if not success:
        logging.error(f"Ошибка при применении FFmpeg фильтров к аудио: {message}")
    return success

async def extract_frame_and_distort(input_path: str, output_path: str) -> bool:
    """
    Извлекает кадр из видео и применяет к нему искажение.
    """
    try:
        params = get_distortion_params()
        frame_path = f"temp_frame_{random.randint(1000, 9999)}.jpg"
        
        # Извлекаем кадр в вычисляемое время
        extract_command = [
            'ffmpeg',
            '-i', input_path,
            '-ss', str(params['frame_extract_time']),
            '-vframes', '1',
            '-y',
            frame_path
        ]
        
        success, message = await run_ffmpeg_command(extract_command, input_path=input_path, output_path=frame_path)
        if not success:
            logging.error(f"Не удалось извлечь кадр: {message}")
            return False
        
        # Применяем обычный seam carving к кадру, если доступен
        if SEAM_CARVING_AVAILABLE:
            success = await apply_normal_seam_carving(frame_path, output_path)
        else:
            success = await apply_ffmpeg_image_distortion(frame_path, output_path)
        
        if os.path.exists(frame_path):
            os.remove(frame_path)
        
        return success
        
    except Exception as e:
        logging.error(f"Ошибка при извлечении кадра и искажении: {e}")
        return False

async def process_distortion(message: types.Message) -> tuple[bool, str | None, str | None]:
    """
    Обрабатывает запрос на искажение, определяет тип медиа и запускает нужную функцию.
    Возвращает: (успех, путь к файлу, тип медиа)
    """
    # Логируем текущую интенсивность
    logging.info(f"Обработка дисторшна с интенсивностью: {DISTORTION_INTENSITY}")
    
    target_message = message.reply_to_message if message.reply_to_message else message
    media_type = None
    file_id = None
    original_extension = ""
    
    logging.info(f"Анализируем сообщение на наличие медиа...")
    
    if target_message.photo:
        media_type = 'photo'
        file_id = target_message.photo[-1].file_id
        original_extension = ".jpg"
        logging.info(f"Найдено фото: {file_id}")
    elif target_message.video:
        media_type = 'video'
        file_id = target_message.video.file_id
        original_extension = ".mp4"
        logging.info(f"Найдено видео: {file_id}")
    elif target_message.animation:
        media_type = 'animation'
        file_id = target_message.animation.file_id
        original_extension = ".mp4"
        logging.info(f"Найдена анимация: {file_id}")
    elif target_message.sticker:
        if target_message.sticker.is_animated or target_message.sticker.is_video:
            logging.warning("Получен анимированный/видео стикер")
            return False, "Извини, анимированные стикеры и видео-стикеры я искажать не умею.", None
        media_type = 'sticker'
        file_id = target_message.sticker.file_id
        original_extension = ".webp"
        logging.info(f"Найден стикер: {file_id}")
    
    if not file_id:
        logging.warning("Медиа не найдено")
        return False, "Не нашел, что искажать. Ответь на медиафайл или отправь его с подписью.", None

    input_path = f"temp_distort_in_{file_id}{original_extension}"
    
    if not await download_file(file_id, input_path):
        return False, "Не смог скачать файл для искажения.", None

    success = False
    output_path = None
    try:
        if media_type in ['photo', 'sticker']:
            output_path = f"temp_distort_out_{file_id}.jpg"
            
            # Используем обычный seam carving для изображений, если доступен
            if SEAM_CARVING_AVAILABLE:
                success = await apply_normal_seam_carving(input_path, output_path)
            else:
                success = await apply_ffmpeg_image_distortion(input_path, output_path)
            
            if success and media_type == 'sticker':
                media_type = 'photo'
                
        elif media_type in ['video', 'animation']:
            output_path_video = f"temp_distort_out_video_{file_id}.mp4"
            output_path_audio = f"temp_distort_out_audio_{file_id}.aac"
            final_output_path = f"temp_distort_out_final_{file_id}.mp4"

            # Сначала пробуем исказить видео и аудио отдельно, затем объединить
            video_success = await apply_ffmpeg_video_distortion(input_path, output_path_video)
            audio_success = await apply_ffmpeg_audio_distortion(input_path, output_path_audio)

            if video_success and audio_success:
                # Объединяем искаженное видео и аудио
                command = [
                    'ffmpeg',
                    '-i', output_path_video,
                    '-i', output_path_audio,
                    '-c:v', 'copy',
                    '-c:a', 'copy',
                    '-y', final_output_path
                ]
                success, msg = await run_ffmpeg_command(command, output_path=final_output_path)
                if success:
                    output_path = final_output_path
                else:
                    logging.error(f"Не удалось объединить видео и аудио: {msg}")
            elif video_success:
                output_path = output_path_video
                success = True
            elif audio_success:
                logging.warning("Аудио искажено, но видео нет. Попробуем извлечь кадр.")
                success = False
            
            # Если не получилось исказить видео или объединить, извлекаем кадр и искажаем его
            if not success:
                output_path = f"temp_distort_out_frame_{file_id}.jpg"
                success = await extract_frame_and_distort(input_path, output_path)
                if success:
                    media_type = 'photo'
                
            # Удаляем промежуточные файлы
            if os.path.exists(output_path_video):
                os.remove(output_path_video)
            if os.path.exists(output_path_audio):
                os.remove(output_path_audio)
    
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)

    if success and output_path:
        return True, output_path, media_type
    else:
        if output_path and os.path.exists(output_path):
            os.remove(output_path)
        return False, "Что-то пошло не так во время искажения. Попробуй еще раз.", None

# --- Фильтр для команды дисторшн ---

def is_distortion_command(message: types.Message) -> bool:
    """
    Проверяет, является ли сообщение командой дисторшн.
    """
    try:
        from config import BLOCKED_USERS
        if message.from_user.id in BLOCKED_USERS:
            logging.info(f"Пользователь {message.from_user.id} заблокирован")
            return False
        
        if (message.photo or message.video or message.animation or message.sticker):
            if message.caption and "дисторшн" in message.caption.lower():
                logging.info(f"Найдена команда дисторшн в подписи к медиа от {message.from_user.id}")
                return True
        
        if message.text and "дисторшн" in message.text.lower():
            logging.info(f"Найден текст 'дисторшн' от {message.from_user.id}")
            if message.reply_to_message:
                reply_msg = message.reply_to_message
                if (reply_msg.photo or reply_msg.video or reply_msg.animation or reply_msg.sticker):
                    logging.info(f"Команда дисторшн в ответ на медиа от {message.from_user.id}")
                    return True
                else:
                    logging.info(f"Текст 'дисторшн' найден, но reply_to_message не содержит медиа")
            else:
                logging.info(f"Текст 'дисторшн' найден, но нет reply_to_message")
        
        return False
    except Exception as e:
        logging.error(f"Ошибка в фильтре is_distortion_command: {e}")
        return False

# --- Главный обработчик команды ---

async def handle_distortion_request(message: types.Message):
    """
    Основной обработчик команды дисторшн.
    Обрабатывает запрос, применяет искажение и отправляет результат.
    """
    try:
        logging.info(f"Получен запрос на дисторшн от пользователя {message.from_user.id}")
        
        target_message = message.reply_to_message if message.reply_to_message else message
        if not (target_message.photo or target_message.video or target_message.animation or target_message.sticker):
            logging.warning("Не найдено медиа для дисторшна")
            await message.answer("Не нашел медиафайл для искажения. Отправь фото, видео, GIF или стикер с подписью 'дисторшн' или ответь на медиа текстом 'дисторшн'.")
            return
        
        await message.answer("🌀 ща, сука...")
        
        logging.info("Начинаем обработку дисторшна")
        success, result_path_or_error, media_type = await process_distortion(message)
        
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
        logging.error(f"Ошибка в handle_distortion_request: {e}")
        await message.answer("Произошла ошибка при обработке запроса. Попробуй еще раз.")