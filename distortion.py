import os
import asyncio
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
    Применяет случайные эффекты искажения к изображению с помощью Pillow.
    """
    try:
        with Image.open(input_path) as img:
            # Конвертируем в RGB для совместимости с эффектами
            img = img.convert("RGB")
            
            # 1. Сдвиг каналов для "глючного" эффекта
            r, g, b = img.split()
            r_offset = random.randint(-10, 10)
            g_offset = random.randint(-10, 10)
            
            # Создаем пустые изображения для сдвинутых каналов
            r = r.transform(img.size, Image.AFFINE, (1, 0, r_offset, 0, 1, 0))
            g = g.transform(img.size, Image.AFFINE, (1, 0, g_offset, 0, 1, 0))
            
            img = Image.merge("RGB", (r, g, b))

            # 2. Добавление случайных горизонтальных линий
            for _ in range(random.randint(5, 15)):
                y = random.randint(0, img.height - 1)
                for x in range(img.width):
                    if random.random() > 0.95: # Не сплошная линия
                         img.putpixel((x, y), (random.randint(0,255), random.randint(0,255), random.randint(0,255)))

            # 3. Применение случайного фильтра
            if random.random() > 0.5:
                img = img.filter(ImageFilter.SHARPEN)
            else:
                enhancer = ImageEnhance.Contrast(img)
                img = enhancer.enhance(random.uniform(1.2, 1.8))

            # 4. Сохраняем с небольшим сжатием для артефактов
            img.save(output_path, "JPEG", quality=random.randint(60, 85))
        return True
    except Exception as e:
        logging.error(f"Ошибка при искажении изображения: {e}")
        return False

async def distort_video(input_path: str, output_path: str) -> bool:
    """
    Искажает видео или GIF с помощью ffmpeg.
    ВАЖНО: ffmpeg должен быть установлен на сервере, где работает бот.
    """
    try:
        # Набор случайных фильтров для ffmpeg
        filters = [
            # Добавляет шум и сдвигает цвета
            "noise=alls=10:allf=t,hue=H='2*PI*t':s=2", 
            # Пикселизация
            "scale=iw/4:ih/4,scale=iw*4:ih*4:flags=neighbor",
            # Изменение контраста и гаммы
            "eq=contrast=1.5:gamma=1.5",
            # Случайные сдвиги полей
            "il=l=random(1,2)*mod(n,2):c=random(1,2)*mod(n,2)"
        ]
        chosen_filter = random.choice(filters)
        
        # Команда для ffmpeg
        command = [
            'ffmpeg',
            '-i', input_path,
            '-vf', chosen_filter,
            '-y',  # Перезаписать выходной файл, если он существует
            '-c:a', 'copy', # Копировать аудиодорожку без перекодирования
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

# --- Главная функция-обработчик ---

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
            success = await distort_image(input_path, output_path)
            # Для стикеров меняем тип на фото, т.к. отправляем как jpg
            if success: media_type = 'photo'
        elif media_type in ['video', 'animation']:
            output_path = f"temp_distort_out_{file_id}.mp4"
            success = await distort_video(input_path, output_path)
    
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