import os
import random
import asyncio
import tempfile
import logging
import subprocess
from aiogram import types, Bot
from aiogram.types import FSInputFile

from moviepy.editor import VideoFileClip, concatenate_videoclips
import moviepy.video.fx.all as vfx
import moviepy.audio.fx.all as afx
from config import chat_settings


TARGET_DURATION = 10
MAX_FILE_SIZE_MB = 50
MAX_INPUT_DURATION_SEC = 120
SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".gif", ".ogg"}

_ytp_semaphore = asyncio.Semaphore(1)


YTP_PRESET_POOLS = {
    "soft": {
        "effects": ["mirror", "mirror_y", "zoom_punch", "freeze_frame", "slowmo", "reverse", "normal"],
        "weights": [15,       15,         20,           20,            10,       10,       10],
    },
    "normal": {
        "effects": ["stutter", "ping_pong", "reverse", "invert", "earrape", "speedup", "slowmo",
                    "mirror", "zoom_punch", "rotate", "freeze_frame", "strobe", "triple_repeat",
                    "mirror_y", "brightness_flash", "silence", "normal"],
        "weights": [10, 8, 8, 8, 8, 7, 5, 8, 16, 7, 16, 4, 14, 7, 7, 6, 9],
    },
    "chaos": {
        "effects": ["stutter", "ping_pong", "earrape", "speedup", "strobe",
                    "triple_repeat", "brightness_flash", "invert", "zoom_punch", "reverse"],
        "weights": [20,        15,          20,         15,       10,
                    20,             15,                15,       15,          10],
    },
    "hell": {
        "effects": ["stutter", "ping_pong", "earrape", "speedup", "strobe",
                    "triple_repeat", "brightness_flash", "invert", "zoom_punch",
                    "freeze_frame", "mirror", "mirror_y", "rotate"],
        "weights": [25, 20, 25, 20, 15, 25, 20, 15, 20, 15, 10, 10, 10],
    },
}


def _make_ytp_sync(
    input_path: str,
    output_path: str,
    target_duration: int = 10,
    preset: str = "normal",
) -> None:
    clip = None
    final_clip = None
    clips = []

    try:
        clip = VideoFileClip(input_path)
        duration = clip.duration

        if duration < 3:
            raise ValueError("Видео слишком короткое (как и твой хуй).")

        current_time = 0.0

        pool_cfg = YTP_PRESET_POOLS.get(preset, YTP_PRESET_POOLS["normal"])
        effects_pool = pool_cfg["effects"]
        effects_weights = pool_cfg["weights"]

        # Для "hell" — куски покороче
        chunk_min = 0.1 if preset == "hell" else 0.3
        chunk_max = 0.8 if preset == "hell" else 1.5

        while current_time < target_duration:
            chunk_len = random.uniform(chunk_min, chunk_max)
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
                    # Если есть аудио — перегружаем звук и выкручиваем цвета
                    if snippet.audio is not None:
                        snippet = snippet.fx(afx.volumex, 10.0).fx(vfx.colorx, 2.0)
                    else:
                        # Если звука нет (например, это GIF), делаем только визуальный глитч
                        snippet = snippet.fx(vfx.colorx, 2.0)

                elif effect == "speedup":
                    # Ускорение от 2 до 4 раз
                    snippet = snippet.fx(vfx.speedx, random.uniform(2.0, 4.0))

                elif effect == "slowmo":
                    # Замедление с понижением тона голоса (MoviePy сам тянет звук)
                    snippet = snippet.fx(vfx.speedx, 0.5)

                elif effect == "mirror":
                    # Отзеркаливание по горизонтали
                    snippet = snippet.fx(vfx.mirror_x)

                elif effect == "zoom_punch":
                    # Резкий зум в случайную область кадра
                    w, h = snippet.size
                    x_center = random.uniform(0.25, 0.75)
                    y_center = random.uniform(0.25, 0.75)
                    zoom = random.uniform(1.5, 3.0)
                    new_w, new_h = int(w / zoom), int(h / zoom)
                    x1 = int((w - new_w) * x_center)
                    y1 = int((h - new_h) * y_center)
                    snippet = (
                        snippet.fx(vfx.crop, x1=x1, y1=y1, width=new_w, height=new_h).resize((w, h))
                    )

                elif effect == "rotate":
                    angle = random.choice([7, 15, 90, 180, 173, -23, -90])
                    snippet = snippet.fx(vfx.rotate, angle)

                elif effect == "freeze_frame":
                    # Делает короткую "заморозку" случайного кадра
                    t = random.uniform(0, max(0.01, snippet.duration * 0.9))
                    snippet = snippet.to_ImageClip(t=t).set_duration(random.uniform(0.2, 0.6))

                elif effect == "strobe":
                    # Психоделическое мерцание (редко)
                    import numpy as np

                    def strobe_effect(frame):
                        if int(frame.mean()) % 2 == 0:
                            return np.zeros_like(frame)
                        return frame

                    snippet = snippet.fl_image(strobe_effect)

                elif effect == "triple_repeat":
                    repeats = random.randint(3, 6)
                    snippet = concatenate_videoclips([snippet] * repeats)

                elif effect == "mirror_y":
                    snippet = snippet.fx(vfx.mirror_y)

                elif effect == "brightness_flash":
                    factor = random.choice([0.1, 4.0, 5.0])
                    snippet = snippet.fx(vfx.colorx, factor)

                elif effect == "silence":
                    snippet = snippet.without_audio()

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
            codec="libvpx-vp9",
            audio_codec="libvorbis",
            temp_audiofile=output_path + ".ogg",
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

def _is_audio_document(document: types.Document) -> bool:
    if document.mime_type == "audio/ogg":
        return True
    if document.file_name:
        ext = os.path.splitext(document.file_name)[1].lower()
        return ext == ".ogg"
    return False


async def run_command(command: list[str]) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return False, stderr.decode(errors="ignore")
    return True, stdout.decode(errors="ignore")


async def convert_tgs_to_webm(input_tgs: str, output_webm: str) -> bool:
    cmd = [
        "/root/upupa/venv/bin/lottie_convert.py",
        input_tgs,
        output_webm,
        "-of",
        "video",
        "--video-format",
        "webm",
        "--fps",
        "30",
        "--sanitize",
    ]
    success, _ = await run_command(cmd)
    return success

async def convert_webm_to_mp4(input_webm: str, output_mp4: str) -> bool:
    cmd = [
        "ffmpeg",
        "-i",
        input_webm,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-y",
        output_mp4,
    ]
    success, _ = await run_command(cmd)
    return success




async def convert_audio_to_mp4(input_audio: str, output_mp4: str) -> bool:
    cmd = [
        "ffmpeg",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=720x1280:r=30",
        "-i",
        input_audio,
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-y",
        output_mp4,
    ]
    success, _ = await run_command(cmd)
    return success
async def handle_ytp_command(message: types.Message, bot: Bot) -> None:
    video_source = None

    # Проверяем реплай
    if message.reply_to_message:
        source = message.reply_to_message
        if source.video or source.animation or source.audio or source.voice or source.sticker is not None or (source.document and (_is_video_document(source.document) or _is_audio_document(source.document))):
            video_source = source

    # Проверяем само сообщение
    if video_source is None and (message.video or message.animation or message.audio or message.voice or message.sticker is not None or (message.document and (_is_video_document(message.document) or _is_audio_document(message.document)))):
        video_source = message

    if not video_source:
        await message.reply("Реплайни блядь на видео/гифку/.ogg или отправь их с подписью «пуп».")
        return

    # Достаем объект файла (видео, гифка, документ или стикер)
    file_obj = video_source.video or video_source.animation or video_source.audio or video_source.voice or video_source.document or video_source.sticker

    if file_obj.file_size and file_obj.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await message.reply(f"Да пошел ты нахуй, файл слишком большой. Максимум {MAX_FILE_SIZE_MB} МБ.")
        return

    if video_source.video and video_source.video.duration:
        if video_source.video.duration > MAX_INPUT_DURATION_SEC:
            await message.reply(f"Видео слишком длинное. Максимум {MAX_INPUT_DURATION_SEC} секунд.")
            return

    if video_source.audio and video_source.audio.duration:
        if video_source.audio.duration > MAX_INPUT_DURATION_SEC:
            await message.reply(f"Аудио слишком длинное. Максимум {MAX_INPUT_DURATION_SEC} секунд.")
            return

    if video_source.voice and video_source.voice.duration:
        if video_source.voice.duration > MAX_INPUT_DURATION_SEC:
            await message.reply(f"Голосовое слишком длинное. Максимум {MAX_INPUT_DURATION_SEC} секунд.")
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
    converted_input_path = None
    output_path = None
    mp4_path = None

    try:
        async with _ytp_semaphore:
            if video_source.sticker and video_source.sticker.is_animated:
                suffix = ".tgs"
            elif video_source.sticker and video_source.sticker.is_video:
                suffix = ".webm"
            elif video_source.animation:
                suffix = ".webm"
            elif video_source.document:
                suffix = os.path.splitext(video_source.document.file_name or "")[1].lower() or ".mp4"
            elif video_source.audio or video_source.voice:
                suffix = ".ogg"
            else:
                suffix = ".mp4"

            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="ytp_in_") as in_file:
                input_path = in_file.name
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm", prefix="ytp_out_") as out_file:
                output_path = out_file.name

            file_info = await bot.get_file(file_obj.file_id)
            await bot.download_file(file_info.file_path, input_path)

            real_input_path = input_path
            if suffix == ".tgs":
                converted_input_path = input_path + "_converted.webm"
                converted = await convert_tgs_to_webm(input_path, converted_input_path)
                if not converted:
                    await processing_msg.delete()
                    await message.reply("❌ Не удалось конвертировать TGS в видео.")
                    return
                real_input_path = converted_input_path
            elif suffix == ".ogg":
                converted_input_path = input_path + "_converted.mp4"
                converted = await convert_audio_to_mp4(input_path, converted_input_path)
                if not converted:
                    await processing_msg.delete()
                    await message.reply("❌ Не удалось конвертировать .ogg в видео.")
                    return
                real_input_path = converted_input_path

            loop = asyncio.get_running_loop()
            chat_id_str = str(message.chat.id)
            chat_cfg = chat_settings.get(chat_id_str, {})
            target_dur = chat_cfg.get("ytp_duration", TARGET_DURATION)
            preset = chat_cfg.get("ytp_preset", "normal")

            await loop.run_in_executor(
                None, _make_ytp_sync, real_input_path, output_path, target_dur, preset
            )

            mp4_path = output_path.replace(".webm", ".mp4")
            converted_to_mp4 = await convert_webm_to_mp4(output_path, mp4_path)
            if converted_to_mp4 and os.path.exists(mp4_path):
                await message.reply_video(
                    FSInputFile(mp4_path, filename="pup.mp4"),
                )
            else:
                await message.reply_document(
                    FSInputFile(output_path, filename="pup.webm"),
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
        for path in (input_path, converted_input_path, output_path, mp4_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    logging.info(f"[ytp] Удалён временный файл: {path}")
                except Exception as exc:
                    logging.warning(f"[ytp] Не удалось удалить {path}: {exc}")
