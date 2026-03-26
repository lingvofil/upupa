import os
import random
import asyncio
import tempfile
import logging
from aiogram import types, Bot
from aiogram.types import FSInputFile

from moviepy.editor import VideoFileClip, concatenate_videoclips
import moviepy.video.fx.all as vfx
import moviepy.audio.fx.all as afx


TARGET_DURATION = 10
MAX_FILE_SIZE_MB = 100
MAX_INPUT_DURATION_SEC = 120
SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

_ytp_semaphore = asyncio.Semaphore(1)


def _make_ytp_sync(input_path: str, output_path: str) -> None:
    clip = None
    final_clip = None
    clips = []

    try:
        clip = VideoFileClip(input_path)
        duration = clip.duration

        if duration < 3:
            raise ValueError("Видео слишком короткое (как и твой хуй).")

        current_time = 0.0

        # Список эффектов и их "веса" (шанс выпадения).
        # Можно менять цифры, чтобы сделать видео более или менее безумным.
        effects_pool = ["stutter", "ping_pong", "reverse", "invert", "earrape", "speedup", "slowmo", "mirror", "normal"]
        effects_weights = [15, 10, 10, 10, 10, 10, 5, 10, 20]

        while current_time < TARGET_DURATION:
            # В YTP нарезки обычно более рваные и короткие, поэтому уменьшаем длину куска
            chunk_len = random.uniform(0.3, 1.5)
            max_start = max(0.0, duration - chunk_len)
            start = random.uniform(0.0, max_start)
            end = min(duration, start + chunk_len)

            if end - start < 0.1:
                continue

            snippet = clip.subclip(start, end)

            # Выбираем эффект с учетом весов
            effect = random.choices(effects_pool, weights=effects_weights, k=1)[0]

            try:
                if effect == "stutter":
                    # Классическое "пулеметное" YTP заикание из второго скрипта
                    stutter_duration = random.uniform(0.05, 0.15)
                    piece = snippet.subclip(0, min(stutter_duration, snippet.duration))
                    repeats = int(snippet.duration / piece.duration)
                    if repeats > 0:
                        snippet = concatenate_videoclips([piece] * repeats)

                elif effect == "ping_pong":
                    # Эффект "Sus" (Вперед -> Назад -> Вперед)
                    rev = snippet.fx(vfx.time_mirror)
                    snippet = concatenate_videoclips([snippet, rev, snippet])

                elif effect == "reverse":
                    # Просто проигрывание задом наперед
                    snippet = snippet.fx(vfx.time_mirror)

                elif effect == "invert":
                    # Инверсия цветов (негатив)
                    snippet = snippet.fx(vfx.invert_colors)

                elif effect == "earrape":
                    # Жесткий перегруз по звуку + легкий визуальный глитч (усиление цвета)
                    snippet = snippet.fx(afx.volumex, 10.0).fx(vfx.colorx, 2.0)

                elif effect == "speedup":
                    # Ускорение от 2 до 4 раз
                    snippet = snippet.fx(vfx.speedx, random.uniform(2.0, 4.0))

                elif effect == "slowmo":
                    # Замедление с понижением тона голоса (MoviePy сам тянет звук)
                    snippet = snippet.fx(vfx.speedx, 0.5)

                elif effect == "mirror":
                    # Отзеркаливание по горизонтали
                    snippet = snippet.fx(vfx.mirror_x)

                # Если effect == "normal", ничего не делаем, кусок остается обычным
            except Exception as exc:
                logging.warning(f"[ytp] Эффект '{effect}' не сработал: {exc}")

            clips.append(snippet)
            current_time += snippet.duration

        if not clips:
            raise RuntimeError("Не удалось нарезать ни одного клипа.")

        final_clip = concatenate_videoclips(clips)
        final_clip.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            fps=30,
            preset="ultrafast",
            threads=2,
            logger=None,
        )
    finally:
        for snippet in clips:
            try:
                snippet.close()
            except Exception:
                pass

        if final_clip is not None:
            try:
                final_clip.close()
            except Exception:
                pass

        if clip is not None:
            try:
                clip.close()
            except Exception:
                pass


def _is_video_document(document: types.Document) -> bool:
    if document.mime_type and document.mime_type.startswith("video/"):
        return True
    if document.file_name:
        ext = os.path.splitext(document.file_name)[1].lower()
        return ext in SUPPORTED_EXTENSIONS
    return False


async def handle_ytp_command(message: types.Message, bot: Bot) -> None:
    video_source = None

    if message.reply_to_message:
        source = message.reply_to_message
        if source.video or (source.document and _is_video_document(source.document)):
            video_source = source

    if video_source is None and (message.video or (message.document and _is_video_document(message.document))):
        video_source = message

    if not video_source:
        await message.reply("Реплайни блядь на видео или отправь видео с подписью «пуп».")
        return

    file_obj = video_source.video or video_source.document

    if file_obj.file_size and file_obj.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await message.reply(f"Файл слишком большой. Максимум {MAX_FILE_SIZE_MB} МБ.")
        return

    if video_source.video and video_source.video.duration:
        if video_source.video.duration > MAX_INPUT_DURATION_SEC:
            await message.reply(f"Видео слишком длинное. Максимум {MAX_INPUT_DURATION_SEC} секунд.")
            return

    if video_source.document:
        file_name = video_source.document.file_name or ""
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            await message.reply(
                f"Неподдерживаемый формат. Подходят: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )
            return

    if _ytp_semaphore.locked():
        await message.reply("Уже шинкую одно видео, подожди немного.")
        return

    processing_msg = await message.reply("⚙️ Пупизирую. пу пу пу...")
    input_path = None
    output_path = None

    try:
        async with _ytp_semaphore:
            if video_source.document:
                suffix = os.path.splitext(video_source.document.file_name or "")[1].lower() or ".mp4"
            else:
                suffix = ".mp4"

            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="ytp_in_") as in_file:
                input_path = in_file.name
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4", prefix="ytp_out_") as out_file:
                output_path = out_file.name

            file_info = await bot.get_file(file_obj.file_id)
            await bot.download_file(file_info.file_path, input_path)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _make_ytp_sync, input_path, output_path)

            await message.reply_video(
                FSInputFile(output_path, filename="pup.mp4"),
                caption="🎬 ВАШ ПУП",
            )

        await processing_msg.delete()

    except ValueError as exc:
        await processing_msg.delete()
        await message.reply(f"❌ {exc}")
    except Exception as exc:
        logging.error(f"[ytp] Ошибка обработки: {exc}", exc_info=True)
        await processing_msg.delete()
        await message.reply("❌ Что-то пошло не так при пупизации.")
    finally:
        for path in (input_path, output_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    logging.info(f"[ytp] Удалён временный файл: {path}")
                except Exception as exc:
                    logging.warning(f"[ytp] Не удалось удалить {path}: {exc}")
