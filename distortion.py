import os
import asyncio
import json
import random
import logging
import subprocess
import numpy as np
from aiogram import types
from aiogram.types import FSInputFile, BufferedInputFile
from PIL import Image, ImageFilter, ImageEnhance, ImageOps
from scipy.ndimage import map_coordinates

# Импортируем общие функции и переменные из других модулей
from config import bot
from whatisthere import download_file # Переиспользуем функцию скачивания

# --- Функции искажения ---

def create_distortion_map(width: int, height: int, intensity: float = 0.3) -> tuple[np.ndarray, np.ndarray]:
    """
    Создает карту искажений для нелинейного преобразования изображения.
    """
    # Создаем сетку координат
    x, y = np.meshgrid(np.arange(width), np.arange(height))
    
    # Нормализуем координаты к диапазону [-1, 1]
    x_norm = (x / width - 0.5) * 2
    y_norm = (y / height - 0.5) * 2
    
    # Создаем радиальные координаты
    r = np.sqrt(x_norm**2 + y_norm**2)
    
    # Различные типы искажений
    distortion_type = random.choice(['liquid', 'wave', 'swirl', 'bulge', 'pinch'])
    
    if distortion_type == 'liquid':
        # Liquid-эффект с волнами
        wave_freq = random.uniform(3, 8)
        wave_amp = intensity * random.uniform(0.8, 1.5)
        
        x_distorted = x + wave_amp * width * np.sin(wave_freq * y_norm) * np.cos(wave_freq * x_norm)
        y_distorted = y + wave_amp * height * np.cos(wave_freq * x_norm) * np.sin(wave_freq * y_norm)
        
    elif distortion_type == 'wave':
        # Волновое искажение
        wave_length = random.uniform(0.1, 0.3)
        wave_amp = intensity * random.uniform(20, 50)
        
        x_distorted = x + wave_amp * np.sin(2 * np.pi * y / (height * wave_length))
        y_distorted = y + wave_amp * np.cos(2 * np.pi * x / (width * wave_length))
        
    elif distortion_type == 'swirl':
        # Закручивающее искажение
        angle = intensity * random.uniform(1, 3) * r
        cos_a, sin_a = np.cos(angle), np.sin(angle)
        
        x_centered = x - width/2
        y_centered = y - height/2
        
        x_distorted = x_centered * cos_a - y_centered * sin_a + width/2
        y_distorted = x_centered * sin_a + y_centered * cos_a + height/2
        
    elif distortion_type == 'bulge':
        # Выпуклое искажение
        strength = intensity * random.uniform(0.3, 0.7)
        bulge_factor = 1 + strength * np.exp(-r * 2)
        
        x_distorted = (x - width/2) * bulge_factor + width/2
        y_distorted = (y - height/2) * bulge_factor + height/2
        
    else:  # pinch
        # Сжимающее искажение
        strength = intensity * random.uniform(0.5, 1.0)
        pinch_factor = 1 - strength * np.exp(-r * 3)
        
        x_distorted = (x - width/2) * pinch_factor + width/2
        y_distorted = (y - height/2) * pinch_factor + height/2
    
    return x_distorted, y_distorted

def apply_advanced_distortion(image: Image.Image, intensity: float = 0.4) -> Image.Image:
    """
    Применяет продвинутое искажение к изображению.
    """
    # Конвертируем в numpy array
    img_array = np.array(image)
    height, width = img_array.shape[:2]
    
    # Создаем карту искажений
    x_distorted, y_distorted = create_distortion_map(width, height, intensity)
    
    # Применяем искажение к каждому каналу
    if len(img_array.shape) == 3:  # RGB
        distorted_array = np.zeros_like(img_array)
        for channel in range(img_array.shape[2]):
            distorted_array[:, :, channel] = map_coordinates(
                img_array[:, :, channel], 
                [y_distorted, x_distorted], 
                order=1, 
                mode='reflect'
            )
    else:  # Grayscale
        distorted_array = map_coordinates(
            img_array, 
            [y_distorted, x_distorted], 
            order=1, 
            mode='reflect'
        )
    
    return Image.fromarray(distorted_array.astype(np.uint8))

def apply_compression_artifacts(image: Image.Image) -> Image.Image:
    """
    Добавляет артефакты сжатия для более реалистичного эффекта.
    """
    # Случайное сжатие JPEG с низким качеством
    import io
    
    # Первое сжатие
    quality1 = random.randint(15, 35)
    buffer1 = io.BytesIO()
    image.save(buffer1, format='JPEG', quality=quality1)
    buffer1.seek(0)
    compressed1 = Image.open(buffer1)
    
    # Второе сжатие для усиления артефактов
    quality2 = random.randint(25, 45)
    buffer2 = io.BytesIO()
    compressed1.save(buffer2, format='JPEG', quality=quality2)
    buffer2.seek(0)
    compressed2 = Image.open(buffer2)
    
    return compressed2

async def distort_image(input_path: str, output_path: str) -> bool:
    """
    Применяет агрессивное искажение к изображению.
    """
    try:
        with Image.open(input_path) as img:
            # Конвертируем в RGB для совместимости
            img = img.convert("RGB")
            
            # Применяем несколько этапов искажения
            
            # Этап 1: Изменение размера для создания пиксельного эффекта
            original_size = img.size
            
            # Случайное сжатие (более агрессивное)
            compression_factors = [0.3, 0.4, 0.5, 0.6]
            compression_factor = random.choice(compression_factors)
            
            small_size = (
                int(original_size[0] * compression_factor),
                int(original_size[1] * compression_factor)
            )
            
            # Сжимаем с размытием
            img_small = img.resize(small_size, Image.LANCZOS)
            
            # Возвращаем к оригинальному размеру с пиксельным эффектом
            img_pixelated = img_small.resize(original_size, Image.NEAREST)
            
            # Этап 2: Продвинутое геометрическое искажение
            distortion_intensity = random.uniform(0.3, 0.6)
            img_distorted = apply_advanced_distortion(img_pixelated, distortion_intensity)
            
            # Этап 3: Дополнительные эффекты
            effects_to_apply = random.randint(2, 4)
            
            for _ in range(effects_to_apply):
                effect = random.choice([
                    'contrast', 'saturation', 'sharpness', 'blur', 'noise', 'hue'
                ])
                
                if effect == 'contrast':
                    enhancer = ImageEnhance.Contrast(img_distorted)
                    img_distorted = enhancer.enhance(random.uniform(0.5, 2.0))
                
                elif effect == 'saturation':
                    enhancer = ImageEnhance.Color(img_distorted)
                    img_distorted = enhancer.enhance(random.uniform(0.3, 2.5))
                
                elif effect == 'sharpness':
                    enhancer = ImageEnhance.Sharpness(img_distorted)
                    img_distorted = enhancer.enhance(random.uniform(0.5, 3.0))
                
                elif effect == 'blur':
                    blur_radius = random.uniform(0.5, 2.0)
                    img_distorted = img_distorted.filter(ImageFilter.GaussianBlur(blur_radius))
                
                elif effect == 'noise':
                    # Добавляем шум
                    img_array = np.array(img_distorted)
                    noise = np.random.normal(0, random.uniform(5, 20), img_array.shape)
                    img_noisy = np.clip(img_array + noise, 0, 255).astype(np.uint8)
                    img_distorted = Image.fromarray(img_noisy)
                
                elif effect == 'hue':
                    # Изменяем цветовые каналы
                    img_array = np.array(img_distorted)
                    if len(img_array.shape) == 3:
                        # Случайно переставляем каналы
                        channels = [0, 1, 2]
                        random.shuffle(channels)
                        img_distorted = Image.fromarray(img_array[:, :, channels])
            
            # Этап 4: Артефакты сжатия
            if random.random() > 0.3:
                img_distorted = apply_compression_artifacts(img_distorted)
            
            # Этап 5: Финальная обработка
            if random.random() > 0.5:
                # Инвертируем цвета с определенной вероятностью
                if random.random() > 0.8:
                    img_distorted = ImageOps.invert(img_distorted)
                
                # Эквализация гистограммы
                if random.random() > 0.6:
                    img_distorted = ImageOps.equalize(img_distorted)
            
            # Сохраняем с низким качеством для дополнительных артефактов
            save_quality = random.randint(40, 70)
            img_distorted.save(output_path, "JPEG", quality=save_quality)
            
        return True
    except Exception as e:
        logging.error(f"Ошибка при искажении изображения: {e}")
        return False

async def distort_video(input_path: str, output_path: str) -> bool:
    """
    Искажает видео с помощью ffmpeg с более агрессивными фильтрами.
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
        
        # Создаем более агрессивные фильтры
        filters = []
        
        # Пиксельный эффект
        scale_factor = random.uniform(0.2, 0.5)
        pixel_width = int(original_width * scale_factor)
        pixel_height = int(original_height * scale_factor)
        
        filters.append(f"scale={pixel_width}:{pixel_height}")
        filters.append(f"scale={original_width}:{original_height}:flags=neighbor")
        
        # Искажение цветов
        filters.append(f"eq=contrast={random.uniform(0.5, 2.0)}:saturation={random.uniform(0.3, 2.5)}:brightness={random.uniform(-0.2, 0.2)}")
        
        # Добавляем шум
        filters.append(f"noise=alls={random.randint(20, 60)}:allf=t")
        
        # Размытие или резкость
        if random.random() > 0.5:
            filters.append(f"gblur=sigma={random.uniform(0.5, 2.0)}")
        else:
            filters.append(f"unsharp=5:5:{random.uniform(1.0, 3.0)}:5:5:0.0")
        
        # Искажение геометрии (если поддерживается)
        if random.random() > 0.5:
            # Волновое искажение
            wave_strength = random.uniform(5, 20)
            wave_freq = random.uniform(0.1, 0.3)
            filters.append(f"delogo=x={int(original_width*0.1)}:y={int(original_height*0.1)}:w={int(original_width*0.8)}:h={int(original_height*0.8)}:show=0")
        
        # Объединяем фильтры
        video_filter = ",".join(filters)
        
        # Команда для ffmpeg
        command = [
            'ffmpeg',
            '-i', input_path,
            '-vf', video_filter,
            '-c:v', 'libx264',
            '-crf', str(random.randint(28, 35)),  # Высокое сжатие
            '-preset', 'fast',
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

# --- Альтернативный метод для экстремального искажения ---

async def extreme_distortion(input_path: str, output_path: str) -> bool:
    """
    Экстремальное искажение изображения с максимальными эффектами.
    """
    try:
        with Image.open(input_path) as img:
            img = img.convert("RGB")
            original_size = img.size
            
            # Множественные циклы искажения
            for cycle in range(random.randint(2, 4)):
                # Очень агрессивное сжатие
                compression_factor = random.uniform(0.15, 0.4)
                small_size = (
                    max(1, int(original_size[0] * compression_factor)),
                    max(1, int(original_size[1] * compression_factor))
                )
                
                # Сжимаем и растягиваем
                img = img.resize(small_size, Image.LANCZOS)
                img = img.resize(original_size, Image.NEAREST)
                
                # Применяем геометрическое искажение
                distortion_intensity = random.uniform(0.5, 0.8)
                img = apply_advanced_distortion(img, distortion_intensity)
                
                # Случайные эффекты
                for _ in range(random.randint(3, 5)):
                    effect_type = random.choice(['contrast', 'color', 'invert', 'equalize', 'compress'])
                    
                    if effect_type == 'contrast':
                        enhancer = ImageEnhance.Contrast(img)
                        img = enhancer.enhance(random.uniform(0.3, 3.0))
                    
                    elif effect_type == 'color':
                        enhancer = ImageEnhance.Color(img)
                        img = enhancer.enhance(random.uniform(0.1, 3.0))
                    
                    elif effect_type == 'invert':
                        if random.random() > 0.7:
                            img = ImageOps.invert(img)
                    
                    elif effect_type == 'equalize':
                        if random.random() > 0.5:
                            img = ImageOps.equalize(img)
                    
                    elif effect_type == 'compress':
                        img = apply_compression_artifacts(img)
            
            # Финальное сохранение с низким качеством
            save_quality = random.randint(20, 50)
            img.save(output_path, "JPEG", quality=save_quality)
            
        return True
    except Exception as e:
        logging.error(f"Ошибка в extreme_distortion: {e}")
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
            # Выбираем уровень искажения
            if random.random() > 0.3:
                # Обычное агрессивное искажение
                success = await distort_image(input_path, output_path)
            else:
                # Экстремальное искажение
                success = await extreme_distortion(input_path, output_path)
            
            # Для стикеров меняем тип на фото, т.к. отправляем как jpg
            if success: media_type = 'photo'
        elif media_type in ['video', 'animation']:
            output_path = f"temp_distort_out_{file_id}.mp4"
            # Сначала пробуем ffmpeg для видео
            success = await distort_video(input_path, output_path)
            # Если ffmpeg не работает, пробуем экстремальное искажение на первом кадре
            if not success:
                logging.info("FFmpeg не сработал, пробуем экстремальный метод")
                output_path = f"temp_distort_out_{file_id}.jpg"
                success = await extreme_distortion(input_path, output_path)
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