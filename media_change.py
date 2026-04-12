import asyncio
import logging
import os
import tempfile
from aiogram import Bot, types
from aiogram.types import FSInputFile

MAX_FILE_SIZE_MB = 50
MAX_INPUT_DURATION_SEC = 180

_media_change_semaphore = asyncio.Semaphore(1)


def _is_video_document(document: types.Document) -> bool:
    if document.mime_type and document.mime_type.startswith("video/"):
        return True
    if document.file_name:
        ext = os.path.splitext(document.file_name)[1].lower()
        return ext in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".gif"}
    return False


def _is_ogg_document(document: types.Document) -> bool:
    if document.mime_type == "audio/ogg":
        return True
    if document.file_name:
        ext = os.path.splitext(document.file_name)[1].lower()
        return ext == ".ogg"
    return False


def _extract_media_source(message: types.Message) -> types.Message | None:
    if message.reply_to_message:
        source = message.reply_to_message
        if (
            source.video
            or source.animation
            or source.audio
            or source.voice
            or source.sticker
            or (source.document and (_is_video_document(source.document) or _is_ogg_document(source.document)))
        ):
            return source

    if (
        message.video
        or message.animation
        or message.audio
        or message.voice
        or message.sticker
        or (message.document and (_is_video_document(message.document) or _is_ogg_document(message.document)))
    ):
        return message

    return None


def _get_duration_seconds(source: types.Message) -> int | None:
    if source.video and source.video.duration:
        return source.video.duration
    if source.animation and source.animation.duration:
        return source.animation.duration
    if source.audio and source.audio.duration:
        return source.audio.duration
    if source.voice and source.voice.duration:
        return source.voice.duration
    return None


async def _run_command(command: list[str]) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return False, stderr.decode(errors="ignore")
    return True, stdout.decode(errors="ignore")


async def _convert_tgs_to_webm(input_tgs: str, output_webm: str) -> bool:
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
    success, _ = await _run_command(cmd)
    return success


async def _convert_static_sticker_to_mp4(input_webp: str, output_mp4: str) -> bool:
    cmd = [
        "ffmpeg",
        "-loop",
        "1",
        "-i",
        input_webp,
        "-t",
        "3",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-y",
        output_mp4,
    ]
    success, _ = await _run_command(cmd)
    return success


async def _change_speed_ffmpeg(input_path: str, output_path: str, speed: float, with_audio: bool) -> tuple[bool, str]:
    cmd = [
        "ffmpeg",
        "-i",
        input_path,
        "-vf",
        f"setpts=PTS/{speed}",
    ]

    if with_audio:
        cmd += ["-af", f"atempo={speed}", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]

    cmd += [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-y",
        output_path,
    ]

    return await _run_command(cmd)


async def handle_speed_command(message: types.Message, bot: Bot, speed: float) -> None:
    media_source = _extract_media_source(message)

    if not media_source:
        await message.reply("Реплайни на видео/гифку/войс/аудио или отправь с подписью «быстрее» / «медленнее».")
        return

    file_obj = (
        media_source.video
        or media_source.animation
        or media_source.audio
        or media_source.voice
        or media_source.document
        or media_source.sticker
    )

    if not file_obj:
        await message.reply("Реплайни на видео/гифку/войс/аудио или отправь с подписью «быстрее» / «медленнее».")
        return

    if file_obj.file_size and file_obj.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await message.reply("Файл весит дохуя. Максимум 50 МБ.")
        return

    duration = _get_duration_seconds(media_source)
    if duration and duration > MAX_INPUT_DURATION_SEC:
        await message.reply(f"Слишком длинно. Максимум {MAX_INPUT_DURATION_SEC} секунд.")
        return

    if _media_change_semaphore.locked():
        await message.reply("Я тут вообще-то работаю, отъебись.")
        return

    is_voice_input = bool(media_source.voice)
    is_audio_input = bool(media_source.audio or (media_source.document and _is_ogg_document(media_source.document)))

    processing_msg = await message.reply("⚙️ меняю скорость...")

    input_path = None
    converted_input_path = None
    output_path = None

    try:
        async with _media_change_semaphore:
            if media_source.sticker and media_source.sticker.is_animated:
                input_suffix = ".tgs"
            elif media_source.sticker and media_source.sticker.is_video:
                input_suffix = ".webm"
            elif media_source.sticker:
                input_suffix = ".webp"
            elif media_source.animation:
                input_suffix = ".webm"
            elif media_source.audio or media_source.voice:
                input_suffix = ".ogg"
            elif media_source.document:
                input_suffix = os.path.splitext(media_source.document.file_name or "")[1].lower() or ".mp4"
            else:
                input_suffix = ".mp4"

            with tempfile.NamedTemporaryFile(delete=False, suffix=input_suffix, prefix="spd_in_") as in_file:
                input_path = in_file.name

            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4", prefix="spd_out_") as out_file:
                output_path = out_file.name

            file_info = await bot.get_file(file_obj.file_id)
            await bot.download_file(file_info.file_path, input_path)

            real_input_path = input_path
            if media_source.sticker and media_source.sticker.is_animated:
                converted_input_path = input_path + "_converted.webm"
                converted = await _convert_tgs_to_webm(input_path, converted_input_path)
                if not converted:
                    await message.reply("❌ Не удалось конвертировать TGS в видео.")
                    return
                real_input_path = converted_input_path
            elif media_source.sticker and not media_source.sticker.is_video and not media_source.sticker.is_animated:
                converted_input_path = input_path + "_converted.mp4"
                converted = await _convert_static_sticker_to_mp4(input_path, converted_input_path)
                if not converted:
                    await message.reply("❌ Не удалось обработать стикер.")
                    return
                real_input_path = converted_input_path

            success, ffmpeg_output = await _change_speed_ffmpeg(real_input_path, output_path, speed, with_audio=True)
            if not success:
                success, ffmpeg_output = await _change_speed_ffmpeg(real_input_path, output_path, speed, with_audio=False)

            if not success:
                logging.error("[media_change] ffmpeg error: %s", ffmpeg_output)
                await message.reply("❌ Не удалось поменять скорость.")
                return

            if is_voice_input or is_audio_input:
                await message.reply_audio(FSInputFile(output_path, filename="changed_speed.mp4"))
            else:
                await message.reply_video(FSInputFile(output_path, filename="changed_speed.mp4"))

            await processing_msg.delete()

    except Exception as exc:
        logging.error("[media_change] Ошибка обработки: %s", exc, exc_info=True)
        await message.reply("❌ Что-то пошло не так при смене скорости.")
        try:
            await processing_msg.delete()
        except Exception:
            pass
    finally:
        for path in (input_path, converted_input_path, output_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as exc:
                    logging.warning("[media_change] Не удалось удалить %s: %s", path, exc)


async def handle_slow_command(message: types.Message, bot: Bot) -> None:
    await handle_speed_command(message, bot, 0.5)


async def handle_fast_command(message: types.Message, bot: Bot) -> None:
    await handle_speed_command(message, bot, 2.0)
