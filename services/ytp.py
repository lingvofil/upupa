import os
import random
import asyncio
import tempfile
import logging
import multiprocessing
import queue
from aiogram import types, Bot
from aiogram.types import FSInputFile

from moviepy.editor import AudioFileClip, VideoFileClip, concatenate_audioclips, concatenate_videoclips
import moviepy.video.fx.all as vfx
import moviepy.audio.fx.all as afx
from core.state import chat_settings


TARGET_DURATION = 10
MAX_FILE_SIZE_MB = 50
MAX_INPUT_DURATION_SEC = 120
COMMAND_TIMEOUT_SEC = 60
YTP_RENDER_TIMEOUT_SEC = 180
YTP_NORMALIZE_TIMEOUT_SEC = 90
YTP_NORMALIZE_MIN_FILE_SIZE_MB = 12
YTP_NORMALIZE_MIN_DURATION_SEC = 30
YTP_NORMALIZE_MAX_EDGE = 1280
TELEGRAM_FILE_TIMEOUT_SEC = 90
TELEGRAM_UPLOAD_TIMEOUT_SEC = 120
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


def _make_audio_ytp_sync(
    input_path: str,
    output_path: str,
    target_duration: int = 10,
    preset: str = "normal",
) -> None:
    clip = None
    final_clip = None
    clips = []

    try:
        clip = AudioFileClip(input_path)
        duration = clip.duration

        if duration < 3:
            raise ValueError("Аудио слишком короткое.")

        audio_effects = [
            "stutter",
            "ping_pong",
            "reverse",
            "earrape",
            "speedup",
            "slowmo",
            "triple_repeat",
            "silence",
            "normal",
        ]
        chunk_min = 0.1 if preset == "hell" else 0.3
        chunk_max = 0.8 if preset == "hell" else 1.5
        current_time = 0.0

        while current_time < target_duration:
            chunk_len = random.uniform(chunk_min, chunk_max)
            max_start = max(0.0, duration - chunk_len)
            start = random.uniform(0.0, max_start)
            end = min(duration, start + chunk_len)

            if end - start < 0.1:
                continue

            snippet = clip.subclip(start, end)
            effect = random.choice(audio_effects)

            try:
                if effect == "stutter":
                    stutter_duration = random.uniform(0.05, 0.15)
                    piece = snippet.subclip(0, min(stutter_duration, snippet.duration))
                    repeats = int(snippet.duration / piece.duration)
                    if repeats > 0:
                        snippet = concatenate_audioclips([piece] * repeats)

                elif effect == "ping_pong":
                    rev = snippet.fx(vfx.time_mirror)
                    snippet = concatenate_audioclips([snippet, rev, snippet])

                elif effect == "reverse":
                    snippet = snippet.fx(vfx.time_mirror)

                elif effect == "earrape":
                    snippet = snippet.fx(afx.volumex, 10.0)

                elif effect == "speedup":
                    snippet = snippet.fx(vfx.speedx, random.uniform(2.0, 4.0))

                elif effect == "slowmo":
                    snippet = snippet.fx(vfx.speedx, 0.5)

                elif effect == "triple_repeat":
                    repeats = random.randint(3, 6)
                    snippet = concatenate_audioclips([snippet] * repeats)

                elif effect == "silence":
                    snippet = snippet.fx(afx.volumex, 0.0)
            except Exception as exc:
                logging.warning(f"[ytp] Audio effect '{effect}' failed: {exc}")

            clips.append(snippet)
            current_time += snippet.duration

        if not clips:
            raise RuntimeError("Не удалось нарезать ни одного аудио-фрагмента.")

        final_clip = concatenate_audioclips(clips)
        final_clip.write_audiofile(
            output_path,
            codec="libmp3lame",
            bitrate="128k",
            fps=44100,
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


def _has_supported_ytp_media(message: types.Message) -> bool:
    document = getattr(message, "document", None)
    return bool(
        getattr(message, "video", None)
        or getattr(message, "video_note", None)
        or getattr(message, "animation", None)
        or getattr(message, "audio", None)
        or getattr(message, "voice", None)
        or getattr(message, "sticker", None) is not None
        or (document and (_is_video_document(document) or _is_audio_document(document)))
    )


def _should_normalize_video(file_obj) -> bool:
    file_size = getattr(file_obj, "file_size", 0) or 0
    duration = getattr(file_obj, "duration", 0) or 0
    return bool(
        file_size >= YTP_NORMALIZE_MIN_FILE_SIZE_MB * 1024 * 1024
        or duration >= YTP_NORMALIZE_MIN_DURATION_SEC
    )


async def run_command(command: list[str], timeout: float = COMMAND_TIMEOUT_SEC) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        stdout, stderr = await proc.communicate()
        output = (stderr or stdout).decode(errors="ignore").strip()
        details = f": {output}" if output else ""
        return False, f"Command timed out after {timeout}s ({command[0]}){details}"

    if proc.returncode != 0:
        return False, stderr.decode(errors="ignore")
    return True, stdout.decode(errors="ignore")


async def normalize_video_for_ytp(input_video: str, output_mp4: str) -> bool:
    scale_filter = (
        f"scale='min({YTP_NORMALIZE_MAX_EDGE},iw)':'min({YTP_NORMALIZE_MAX_EDGE},ih)':"
        "force_original_aspect_ratio=decrease,fps=30"
    )
    cmd = [
        "ffmpeg",
        "-i",
        input_video,
        "-vf",
        scale_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "26",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        "-threads",
        "2",
        "-y",
        output_mp4,
    ]
    success, output = await run_command(cmd, timeout=YTP_NORMALIZE_TIMEOUT_SEC)
    if not success:
        logging.warning("[ytp] Не удалось нормализовать входное видео: %s", output)
    return success


def _ytp_worker_entry(result_queue, func_name: str, args: tuple) -> None:
    try:
        globals()[func_name](*args)
        result_queue.put((True, None, None))
    except Exception as exc:
        result_queue.put((False, exc.__class__.__name__, str(exc)))


async def _run_blocking_ytp(func_name: str, *args, timeout: int = YTP_RENDER_TIMEOUT_SEC) -> None:
    start_methods = multiprocessing.get_all_start_methods()
    ctx = multiprocessing.get_context("fork" if "fork" in start_methods else "spawn")
    result_queue = ctx.Queue()
    process = ctx.Process(target=_ytp_worker_entry, args=(result_queue, func_name, args))
    process.start()

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while process.is_alive() and loop.time() < deadline:
        await asyncio.sleep(0.25)

    timed_out = process.is_alive()
    if timed_out:
        process.terminate()
        for _ in range(20):
            process.join(0)
            if not process.is_alive():
                break
            await asyncio.sleep(0.25)

        if process.is_alive():
            process.kill()
            process.join()
        raise asyncio.TimeoutError

    process.join()

    try:
        ok, error_type, error_message = await loop.run_in_executor(None, result_queue.get, True, 1)
    except queue.Empty:
        if process.exitcode == 0:
            return
        raise RuntimeError(f"YTP worker exited with code {process.exitcode}")

    if ok:
        return
    if error_type == "ValueError":
        raise ValueError(error_message)
    raise RuntimeError(error_message)


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
        if _has_supported_ytp_media(source):
            video_source = source

    # Проверяем само сообщение
    if video_source is None and _has_supported_ytp_media(message):
        video_source = message

    if not video_source:
        await message.reply("Реплайни блядь на видео/гифку/.ogg или отправь их с подписью «пуп».")
        return

    # Достаем объект файла (видео, гифка, документ или стикер)
    file_obj = (
        video_source.video
        or getattr(video_source, "video_note", None)
        or video_source.animation
        or video_source.audio
        or video_source.voice
        or video_source.document
        or video_source.sticker
    )

    if file_obj.file_size and file_obj.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await message.reply(f"Да пошел ты нахуй, файл слишком большой. Максимум {MAX_FILE_SIZE_MB} МБ.")
        return

    if video_source.video and video_source.video.duration:
        if video_source.video.duration > MAX_INPUT_DURATION_SEC:
            await message.reply(f"Видео слишком длинное. Максимум {MAX_INPUT_DURATION_SEC} секунд.")
            return

    video_note = getattr(video_source, "video_note", None)
    if video_note and video_note.duration:
        if video_note.duration > MAX_INPUT_DURATION_SEC:
            await message.reply(f"Видеокружок слишком длинный. Максимум {MAX_INPUT_DURATION_SEC} секунд.")
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
    normalized_input_path = None
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

            is_audio_input = bool(
                video_source.audio
                or video_source.voice
                or (video_source.document and _is_audio_document(video_source.document))
            )

            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="ytp_in_") as in_file:
                input_path = in_file.name
            output_suffix = ".mp3" if is_audio_input else ".webm"
            with tempfile.NamedTemporaryFile(delete=False, suffix=output_suffix, prefix="ytp_out_") as out_file:
                output_path = out_file.name

            file_info = await asyncio.wait_for(
                bot.get_file(file_obj.file_id),
                timeout=TELEGRAM_FILE_TIMEOUT_SEC,
            )
            await asyncio.wait_for(
                bot.download_file(file_info.file_path, input_path),
                timeout=TELEGRAM_FILE_TIMEOUT_SEC,
            )

            real_input_path = input_path
            if suffix == ".tgs":
                converted_input_path = input_path + "_converted.webm"
                converted = await convert_tgs_to_webm(input_path, converted_input_path)
                if not converted:
                    await processing_msg.delete()
                    await message.reply("❌ Не удалось конвертировать TGS в видео.")
                    return
                real_input_path = converted_input_path
            elif suffix == ".ogg" and not is_audio_input:
                converted_input_path = input_path + "_converted.mp4"
                converted = await convert_audio_to_mp4(input_path, converted_input_path)
                if not converted:
                    await processing_msg.delete()
                    await message.reply("❌ Не удалось конвертировать .ogg в видео.")
                    return
                real_input_path = converted_input_path

            if not is_audio_input and _should_normalize_video(file_obj):
                normalized_input_path = input_path + "_normalized.mp4"
                normalized = await normalize_video_for_ytp(real_input_path, normalized_input_path)
                if normalized and os.path.exists(normalized_input_path):
                    logging.info(
                        "[ytp] Нормализовано тяжёлое входное видео перед рендером: %s",
                        normalized_input_path,
                    )
                    real_input_path = normalized_input_path
                else:
                    logging.warning("[ytp] Продолжаю с исходным видео без нормализации")

            chat_id_str = str(message.chat.id)
            chat_cfg = chat_settings.get(chat_id_str, {})
            target_dur = chat_cfg.get("ytp_duration", TARGET_DURATION)
            preset = chat_cfg.get("ytp_preset", "normal")

            if is_audio_input:
                await _run_blocking_ytp(
                    "_make_audio_ytp_sync", real_input_path, output_path, target_dur, preset
                )
                await asyncio.wait_for(
                    message.reply_audio(FSInputFile(output_path, filename="pup.mp3")),
                    timeout=TELEGRAM_UPLOAD_TIMEOUT_SEC,
                )
            else:
                await _run_blocking_ytp(
                    "_make_ytp_sync", real_input_path, output_path, target_dur, preset
                )

                mp4_path = output_path.replace(".webm", ".mp4")
                converted_to_mp4 = await convert_webm_to_mp4(output_path, mp4_path)
                if converted_to_mp4 and os.path.exists(mp4_path):
                    await asyncio.wait_for(
                        message.reply_video(FSInputFile(mp4_path, filename="pup.mp4")),
                        timeout=TELEGRAM_UPLOAD_TIMEOUT_SEC,
                    )
                else:
                    await asyncio.wait_for(
                        message.reply_document(FSInputFile(output_path, filename="pup.webm")),
                        timeout=TELEGRAM_UPLOAD_TIMEOUT_SEC,
                    )

        await processing_msg.delete()

    except asyncio.TimeoutError:
        logging.warning("[ytp] Обработка зависла дольше %s секунд", YTP_RENDER_TIMEOUT_SEC)
        await processing_msg.delete()
        await message.reply("❌ Пупизация зависла и была остановлена. Попробуй другое видео или пресет попроще.")
    except ValueError as exc:
        await processing_msg.delete()
        await message.reply(f"❌ {exc}")
    except Exception as exc:
        logging.error(f"[ytp] Ошибка обработки: {exc}", exc_info=True)
        await processing_msg.delete()
        await message.reply("❌ Что-то пошло не так при пупизации.")
    finally:
        for path in (input_path, converted_input_path, normalized_input_path, output_path, mp4_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                    logging.info(f"[ytp] Удалён временный файл: {path}")
                except Exception as exc:
                    logging.warning(f"[ytp] Не удалось удалить {path}: {exc}")
