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
import seam_carving

# Импортируем общие функции и переменные из других модулей
from config import bot
from whatisthere import download_file # Переиспользуем функцию скачивания

# Настройки дисторшн
DEFAULT_DISTORT_PERCENT = 50  # Процент сжатия по умолчанию
MIN_DISTORT_PERCENT = 20      # Минимальный процент сжатия
MAX_DISTORT_PERCENT = 80      # Максимальный процент сжатия

async def apply_seam_carving_distortion(input_path: str, output_path: str, distort_percent: int = DEFAULT_DISTORT_PERCENT) -> bool:
    """
    Применяет дисторшн через seam carving к изображению.
    
    Args:
        input_path: Путь к исходному изображению
        output_path: Путь для сохранения результата
        distort_percent: Процент сжатия (чем больше, тем больше искажение)
    
    Returns:
        bool: True если успешно, False если ошибка
    """
    try:
        # Открываем изображение
        with Image.open(input_path) as img:
            # Конвертируем в RGB для совместимости
            img = img.convert("RGB")
            
            # Преобразуем в numpy array
            src = np.array(img)
            src_h, src_w, _ = src.shape
            
            logging.info(f"Исходное изображение: {src_w}x{src_h}")
            
            # Проверяем минимальные размеры
            if src_w < 50 or src_h < 50:
                logging.warning("Изображение слишком маленькое для seam carving")
                return False
            
            # Вычисляем новые размеры
            new_width = int(src_w - (src_w / 100 * distort_percent))
            new_height = int(src_h - (src_h / 100 * distort_percent))
            
            # Проверяем, что новые размеры не слишком малы
            new_width = max(new_width, 20)
            new_height = max(new_height, 20)
            
            logging.info(f"Новые размеры: {new_width}x{new_height} (сжатие {distort_percent}%)")
            
            # Применяем seam carving
            dst = seam_carving.resize(
                src, 
                (new_width, new_height),
                energy_mode='backward',    # Более качественный режим
                order='width-first',       # Сначала уменьшаем ширину
                keep_mask=None
            )
            
            # Сохраняем результат
            result_img = Image.fromarray(dst)
            result_img.save(output_path, "JPEG", quality=85)
            
            logging.info(f"Дисторшн применен успешно, сохранен в {output_path}")
            return True
            
    except Exception as e:
        logging.error(f"Ошибка при применении seam carving: {e}")
        return False

async def apply_random_seam_carving(input_path: str, output_path: str) -> bool:
    """
    Применяет seam carving со случайным процентом искажения.
    """
    # Случайный процент искажения
    distort_percent = random.randint(MIN_DISTORT_PERCENT, MAX_DISTORT_PERCENT)
    
    # Иногда применяем более экстремальные значения
    if random.random() < 0.2:  # 20% шанс
        distort_percent = random.randint(60, 90)
    
    logging.info(f"Применяем случайный дисторшн с процентом: {distort_percent}%")
    
    return await apply_seam_carving_distortion(input_path, output_path, distort_percent)

async def apply_double_seam_carving(input_path: str, output_path: str) -> bool:
    """
    Применяет двойной seam carving для более экстремального эффекта.
    """
    try:
        # Временный файл для промежуточного результата
        temp_path = f"temp_seam_{random.randint(1000, 9999)}.jpg"
        
        # Первый проход - средний уровень искажения
        first_distort = random.randint(30, 50)
        if not await apply_seam_carving_distortion(input_path, temp_path, first_distort):
            return False
        
        # Второй проход - добавляем еще искажения
        second_distort = random.randint(20, 40)
        success = await apply_seam_carving_distortion(temp_path, output_path, second_distort)
        
        # Удаляем временный файл
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        return success
        
    except Exception as e:
        logging.error(f"Ошибка при двойном seam carving: {e}")
        return False

async def distort_video_with_seam_carving(input_path: str, output_path: str) -> bool:
    """
    Пытается применить дисторшн к видео через ffmpeg, если не получается - извлекает кадр.
    """
    try:
        # Сначала пробуем обычный ffmpeg с простыми фильтрами
        distort_percent = random.randint(MIN_DISTORT_PERCENT, MAX_DISTORT_PERCENT)
        
        # Получаем размеры видео
        probe_command = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', input_path
        ]
        
        probe_process = await asyncio.create_subprocess_exec(
            *probe_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await probe_process.communicate()
        
        if probe_process.returncode != 0:
            logging.error(f"Не удалось получить информацию о видео: {stderr.decode()}")
            return await extract_frame_and_distort(input_path, output_path)
        
        probe_data = json.loads(stdout.decode())
        
        # Ищем видео поток
        video_stream = None
        for stream in probe_data['streams']:
            if stream['codec_type'] == 'video':
                video_stream = stream
                break
        
        if not video_stream:
            logging.error("Не найден видео поток")
            return await extract_frame_and_distort(input_path, output_path)
        
        original_width = video_stream['width']
        original_height = video_stream['height']
        
        # Вычисляем новые размеры
        new_width = int(original_width - (original_width / 100 * distort_percent))
        new_height = int(original_height - (original_height / 100 * distort_percent))
        
        # Команда для ffmpeg с изменением размера и улучшенными фильтрами
        command = [
            'ffmpeg',
            '-i', input_path,
            '-vf', f'scale={new_width}:{new_height},scale={original_width}:{original_height}:flags=neighbor',
            '-c:v', 'libx264',
            '-crf', '28',
            '-preset', 'fast',
            '-y',
            '-c:a', 'copy',
            output_path
        ]
        
        # Запускаем процесс
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logging.error(f"Ошибка ffmpeg: {stderr.decode()}")
            return await extract_frame_and_distort(input_path, output_path)
        
        return True
        
    except FileNotFoundError:
        logging.error("ffmpeg не найден")
        return await extract_frame_and_distort(input_path, output_path)
    except Exception as e:
        logging.error(f"Ошибка при обработке видео: {e}")
        return await extract_frame_and_distort(input_path, output_path)

async def extract_frame_and_distort(input_path: str, output_path: str) -> bool:
    """
    Извлекает кадр из видео и применяет к нему seam carving.
    """
    try:
        # Временный файл для кадра
        frame_path = f"temp_frame_{random.randint(1000, 9999)}.jpg"
        
        # Извлекаем случайный кадр
        extract_command = [
            'ffmpeg',
            '-i', input_path,
            '-ss', '00:00:01',  # Берем кадр с первой секунды
            '-vframes', '1',
            '-y',
            frame_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *extract_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logging.error(f"Не удалось извлечь кадр: {stderr.decode()}")
            return False
        
        # Применяем seam carving к кадру
        success = await apply_random_seam_carving(frame_path, output_path)
        
        # Удаляем временный файл
        if os.path.exists(frame_path):
            os.remove(frame_path)
        
        return success
        
    except Exception as e:
        logging.error(f"Ошибка при извлечении кадра: {e}")
        return False

async def process_distortion(message: types.Message) -> tuple[bool, str | None, str | None]:
    """
    Обрабатывает запрос на искажение, определяет тип медиа и запускает нужную функцию.
    Возвращает: (успех, путь к файлу, тип медиа)
    """
    target_message = message.reply_to_message if message.reply_to_message else message
    media_type = None
    file_id = None
    original_extension = ""
    
    logging.info(f"Анализируем сообщение на наличие медиа...")
    
    # Определяем тип медиа и file_id
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

    # Скачиваем файл
    input_path = f"temp_distort_in_{file_id}{original_extension}"
    output_path = f"temp_distort_out_{file_id}.jpg"
    
    if not await download_file(file_id, input_path):
        return False, "Не смог скачать файл для искажения.", None

    success = False
    try:
        if media_type in ['photo', 'sticker']:
            # Выбираем метод искажения для изображений
            distortion_method = random.choice(['normal', 'double', 'extreme'])
            
            if distortion_method == 'normal':
                success = await apply_random_seam_carving(input_path, output_path)
            elif distortion_method == 'double':
                success = await apply_double_seam_carving(input_path, output_path)
            else:  # extreme
                # Экстремальный дисторшн
                extreme_percent = random.randint(70, 95)
                success = await apply_seam_carving_distortion(input_path, output_path, extreme_percent)
            
            # Для стикеров меняем тип на фото
            if success and media_type == 'sticker':
                media_type = 'photo'
                
        elif media_type in ['video', 'animation']:
            # Для видео сначала пробуем обработать как видео
            output_path = f"temp_distort_out_{file_id}.mp4"
            success = await distort_video_with_seam_carving(input_path, output_path)
            
            # Если не получилось, извлекаем кадр и искажаем его
            if not success:
                output_path = f"temp_distort_out_{file_id}.jpg"
                success = await extract_frame_and_distort(input_path, output_path)
                if success:
                    media_type = 'photo'
    
    finally:
        # Удаляем исходный скачанный файл
        if os.path.exists(input_path):
            os.remove(input_path)

    if success:
        return True, output_path, media_type
    else:
        # Если искажение не удалось, удаляем и выходной файл
        if os.path.exists(output_path):
            os.remove(output_path)
        return False, "Что-то пошло не так во время искажения.", None

# --- Фильтр для команды дисторшн ---

def is_distortion_command(message: types.Message) -> bool:
    """
    Проверяет, является ли сообщение командой дисторшн.
    """
    try:
        # Проверяем, что пользователь не заблокирован
        from config import BLOCKED_USERS
        if message.from_user.id in BLOCKED_USERS:
            logging.info(f"Пользователь {message.from_user.id} заблокирован")
            return False
        
        # Случай 1: Медиа с подписью "дисторшн"
        if (message.photo or message.video or message.animation or message.sticker):
            if message.caption and "дисторшн" in message.caption.lower():
                logging.info(f"Найдена команда дисторшн в подписи к медиа от {message.from_user.id}")
                return True
        
        # Случай 2: Текст "дисторшн" в ответ на медиа
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
        
        # Проверяем, есть ли медиа для обработки
        target_message = message.reply_to_message if message.reply_to_message else message
        if not (target_message.photo or target_message.video or target_message.animation or target_message.sticker):
            logging.warning("Не найдено медиа для дисторшна")
            await message.answer("Не нашел медиафайл для искажения. Отправь фото, видео, GIF или стикер с подписью 'дисторшн' или ответь на медиа текстом 'дисторшн'.")
            return
        
        # Отправляем сообщение о начале обработки
        await message.answer("🌀 Обрабатываю твою фотку...")
        
        # Обрабатываем запрос на искажение
        logging.info("Начинаем обработку дисторшна")
        success, result_path_or_error, media_type = await process_distortion(message)
        
        if not success:
            # Если произошла ошибка, отправляем сообщение об ошибке
            logging.error(f"Ошибка при обработке дисторшна: {result_path_or_error}")
            await message.answer(result_path_or_error)
            return
        
        # Если все прошло успешно, отправляем искаженный файл
        file_path = result_path_or_error
        logging.info(f"Дисторшн готов, отправляем файл: {file_path}, тип: {media_type}")
        
        try:
            # Создаем файл для отправки
            file_to_send = FSInputFile(file_path)
            
            # Отправляем в зависимости от типа медиа
            if media_type == 'photo':
                await message.answer_photo(file_to_send, caption="🌀 Дисторшн готов!")
            elif media_type in ['video', 'animation']:
                await message.answer_video(file_to_send, caption="🌀 Дисторшн готов!")
            
        except Exception as e:
            logging.error(f"Ошибка при отправке искаженного файла: {e}")
            await message.answer("Искажение готово, но не смог отправить файл. Попробуй еще раз.")
        
        finally:
            # Удаляем временный файл
            if os.path.exists(file_path):
                os.remove(file_path)
                
    except Exception as e:
        logging.error(f"Ошибка в handle_distortion_request: {e}")
        await message.answer("Произошла ошибка при обработке запроса. Попробуй еще раз.")