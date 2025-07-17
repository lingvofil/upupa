import os
import asyncio
import json
import random
import logging
import subprocess
from aiogram import types
from aiogram.types import FSInputFile, BufferedInputFile
from PIL import Image, ImageFilter, ImageEnhance

# Импортируем общие функции и переменные из других модулей
from config import bot
from whatisthere import download_file # Переиспользуем функцию скачивания

# --- Функции искажения ---

async def distort_image(input_path: str, output_path: str) -> bool:
    """
    Применяет liquid rescale искажение к изображению с помощью Pillow.
    Имитирует эффект ImageMagick liquid-rescale.
    """
    try:
        with Image.open(input_path) as img:
            # Конвертируем в RGB для совместимости
            img = img.convert("RGB")
            original_size = img.size
            
            # Получаем размеры для liquid rescale (60-80% от оригинала)
            scale_factor = random.uniform(0.6, 0.8)
            liquid_width = int(original_size[0] * scale_factor)
            liquid_height = int(original_size[1] * scale_factor)
            
            # Этап 1: Сжимаем изображение (имитация liquid rescale)
            # Используем LANCZOS для лучшего качества при уменьшении
            img_small = img.resize((liquid_width, liquid_height), Image.LANCZOS)
            
            # Этап 2: Растягиваем обратно до оригинального размера
            # Используем NEAREST для создания "квадратного" эффекта
            img_distorted = img_small.resize(original_size, Image.NEAREST)
            
            # Дополнительные эффекты для усиления искажения
            if random.random() > 0.5:
                # Случайное изменение контраста
                enhancer = ImageEnhance.Contrast(img_distorted)
                img_distorted = enhancer.enhance(random.uniform(1.1, 1.4))
            
            if random.random() > 0.7:
                # Случайное изменение насыщенности
                enhancer = ImageEnhance.Color(img_distorted)
                img_distorted = enhancer.enhance(random.uniform(0.8, 1.3))
            
            # Сохраняем с качеством 85-95% для минимальных артефактов
            img_distorted.save(output_path, "JPEG", quality=random.randint(85, 95))
            
        return True
    except Exception as e:
        logging.error(f"Ошибка при искажении изображения: {e}")
        return False

async def distort_video(input_path: str, output_path: str) -> bool:
    """
    Искажает видео или GIF с помощью ffmpeg, используя liquid rescale эффект.
    ВАЖНО: ffmpeg должен быть установлен на сервере, где работает бот.
    """
    try:
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
            logging.error(f"Ошибка получения информации о видео: {stderr.decode()}")
            return False
        
        import json
        probe_data = json.loads(stdout.decode())
        
        # Ищем видео поток
        video_stream = None
        for stream in probe_data['streams']:
            if stream['codec_type'] == 'video':
                video_stream = stream
                break
        
        if not video_stream:
            logging.error("Не найден видео поток")
            return False
        
        original_width = video_stream['width']
        original_height = video_stream['height']
        
        # Случайный коэффициент сжатия для liquid rescale
        scale_factor = random.uniform(0.6, 0.8)
        liquid_width = int(original_width * scale_factor)
        liquid_height = int(original_height * scale_factor)
        
        # Фильтр для liquid rescale эффекта
        # Сначала уменьшаем, потом увеличиваем обратно
        liquid_filter = f"scale={liquid_width}:{liquid_height},scale={original_width}:{original_height}:flags=neighbor"
        
        # Дополнительные эффекты
        additional_effects = [
            f"eq=contrast={random.uniform(1.1, 1.4)}",
            f"eq=saturation={random.uniform(0.8, 1.3)}",
            f"unsharp=5:5:{random.uniform(0.5, 1.0)}:5:5:0.0"
        ]
        
        # Случайно добавляем один из дополнительных эффектов
        if random.random() > 0.5:
            liquid_filter += f",{random.choice(additional_effects)}"
        
        # Команда для ffmpeg
        command = [
            'ffmpeg',
            '-i', input_path,
            '-vf', liquid_filter,
            '-y',  # Перезаписать выходной файл
            '-c:a', 'copy',  # Копировать аудио без изменений
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
            return False
        return True
    except FileNotFoundError:
        logging.error("ffmpeg не найден. Убедитесь, что он установлен и доступен в PATH.")
        return False
    except Exception as e:
        logging.error(f"Ошибка при искажении видео: {e}")
        return False

# --- Альтернативный метод liquid rescale ---

async def simple_liquid_rescale(input_path: str, output_path: str) -> bool:
    """
    Простой liquid rescale эффект только с помощью Pillow.
    Может использоваться как для изображений, так и для первого кадра видео.
    """
    try:
        with Image.open(input_path) as img:
            img = img.convert("RGB")
            original_size = img.size
            
            # Более агрессивное сжатие для ярко выраженного эффекта
            scale_factors = [0.5, 0.6, 0.7, 0.8]
            scale_factor = random.choice(scale_factors)
            
            # Первый этап - сжатие по горизонтали
            h_compressed_width = int(original_size[0] * scale_factor)
            img_h_compressed = img.resize((h_compressed_width, original_size[1]), Image.LANCZOS)
            
            # Второй этап - сжатие по вертикали
            v_compressed_height = int(original_size[1] * scale_factor)
            img_hv_compressed = img_h_compressed.resize((h_compressed_width, v_compressed_height), Image.LANCZOS)
            
            # Возвращаем к исходному размеру
            img_final = img_hv_compressed.resize(original_size, Image.NEAREST)
            
            # Дополнительные эффекты для усиления
            if random.random() > 0.3:
                # Добавляем легкую резкость
                img_final = img_final.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=2))
            
            img_final.save(output_path, "JPEG", quality=90)
            
        return True
    except Exception as e:
        logging.error(f"Ошибка в simple_liquid_rescale: {e}")
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
        original_extension = ".mp4" # GIF-ки в telegram это mp4 без звука
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
    output_path = f"temp_distort_out_{file_id}.jpg" # Искаженные фото и стикеры будут jpg
    
    if not await download_file(file_id, input_path):
        return False, "Не смог скачать файл для искажения.", None

    success = False
    try:
        if media_type in ['photo', 'sticker']:
            # Для изображений используем liquid rescale
            success = await distort_image(input_path, output_path)
            # Для стикеров меняем тип на фото, т.к. отправляем как jpg
            if success: media_type = 'photo'
        elif media_type in ['video', 'animation']:
            output_path = f"temp_distort_out_{file_id}.mp4"
            # Сначала пробуем ffmpeg для видео
            success = await distort_video(input_path, output_path)
            # Если ffmpeg не работает, пробуем простой метод на первом кадре
            if not success:
                logging.info("FFmpeg не сработал, пробуем простой метод")
                output_path = f"temp_distort_out_{file_id}.jpg"
                success = await simple_liquid_rescale(input_path, output_path)
                if success: media_type = 'photo'  # Меняем тип на фото
    
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