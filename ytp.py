import asyncio
import gc
import json
import logging
import os
import random
import subprocess
import tempfile
from dataclasses import dataclass

from aiogram import Bot, types
from aiogram.types import FSInputFile

TARGET_DURATION = 10
MAX_FILE_SIZE_MB = 50
MAX_INPUT_DURATION_SEC = 120
SEGMENT_CHUNK_SEC = 10
SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

_ytp_semaphore = asyncio.Semaphore(1)


@dataclass
class MediaInfo:
    duration: float


class YTPGenerator:
    def __init__(self, input_path: str, ram_limit_mb: int = 2048) -> None:
        self.input_path = input_path
        self.ram_limit_mb = ram_limit_mb

    def stutter_vf_fragment(self) -> str:
        return "fps=30,loop=3:size=5:start=0"

    def mirror_vf_fragment(self, input_label: str, output_label: str, tag: str) -> str:
        return (
            f"[{input_label}]split=2[{tag}l][{tag}r];"
            f"[{tag}l]crop=iw/2:ih:0:0[{tag}left];"
            f"[{tag}left]hflip[{tag}right];"
            f"[{tag}left][{tag}right]hstack[{output_label}]"
        )

    def screen_shake_vf_fragment(self) -> str:
        return (
            "crop=iw-40:ih-40:20+20*sin(2*PI*t*10):20+20*cos(2*PI*t*10),"
            "scale=iw+40:ih+40"
        )

    def gmajor_af_fragment(self) -> str:
        return "asetrate=44100*0.5,atempo=2.0"

    def _probe(self) -> MediaInfo:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            self.input_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout)
        duration = float(payload["format"]["duration"])
        return MediaInfo(duration=duration)

    def render(self, output_path: str) -> None:
        info = self._probe()
        if info.duration < 3:
            raise ValueError("Видео слишком короткое (как и твой хуй).")

        chunk_points = [0.0]
        if info.duration > SEGMENT_CHUNK_SEC:
            chunk_points = [
                float(i) for i in range(0, int(info.duration), SEGMENT_CHUNK_SEC)
            ]

        filter_parts: list[str] = []
        concat_inputs: list[str] = []
        current_time = 0.0
        idx = 0

        while current_time < TARGET_DURATION:
            chunk_start = random.choice(chunk_points)
            chunk_end = min(info.duration, chunk_start + SEGMENT_CHUNK_SEC)

            chunk_len = random.uniform(0.5, 2.0)
            max_start = max(chunk_start, chunk_end - chunk_len)
            start = random.uniform(chunk_start, max_start)
            end = min(info.duration, start + chunk_len)
            if end - start < 0.1:
                continue

            effect = random.choice(["stutter", "mirror", "gmajor", "shake", "normal"])
            v_in = f"v{idx}in"
            a_in = f"a{idx}in"
            v_out = f"v{idx}out"
            a_out = f"a{idx}out"

            filter_parts.append(
                f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[{v_in}]"
            )
            filter_parts.append(
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[{a_in}]"
            )

            if effect == "stutter":
                filter_parts.append(f"[{v_in}]{self.stutter_vf_fragment()}[{v_out}]")
                filter_parts.append(f"[{a_in}]anull[{a_out}]")
            elif effect == "mirror":
                filter_parts.append(self.mirror_vf_fragment(v_in, v_out, f"m{idx}"))
                filter_parts.append(f"[{a_in}]anull[{a_out}]")
            elif effect == "gmajor":
                filter_parts.append(f"[{v_in}]null[{v_out}]")
                filter_parts.append(f"[{a_in}]{self.gmajor_af_fragment()}[{a_out}]")
            elif effect == "shake":
                shake = self.screen_shake_vf_fragment()
                filter_parts.append(f"[{v_in}]{shake}[{v_out}]")
                filter_parts.append(f"[{a_in}]anull[{a_out}]")
            else:
                filter_parts.append(f"[{v_in}]null[{v_out}]")
                filter_parts.append(f"[{a_in}]anull[{a_out}]")

            concat_inputs.append(f"[{v_out}][{a_out}]")
            current_time += end - start
            idx += 1

            del chunk_start, chunk_end, chunk_len, max_start, start, end, effect
            gc.collect()

        if not concat_inputs:
            raise RuntimeError("Не удалось нарезать ни одного клипа.")

        filter_parts.append(
            "".join(concat_inputs) + f"concat=n={len(concat_inputs)}:v=1:a=1[vout][aout]"
        )
        filter_complex = ";".join(filter_parts)

        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-filter_complex_threads",
            "1",
            "-i",
            self.input_path,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-preset",
            "ultrafast",
            "-threads",
            "1",
            output_path,
        ]

        subprocess.run(ffmpeg_cmd, check=True)


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
            generator = YTPGenerator(input_path=input_path, ram_limit_mb=2048)
            await loop.run_in_executor(None, generator.render, output_path)
            del generator
            gc.collect()

            await message.reply_video(
                FSInputFile(output_path, filename="pup.mp4"),
                caption="🎬 ВАШ ПУП",
            )

        await processing_msg.delete()

    except ValueError as exc:
        await processing_msg.delete()
        await message.reply(f"❌ {exc}")
    except subprocess.CalledProcessError as exc:
        logging.error(f"[ytp] FFmpeg ошибка: {exc}", exc_info=True)
        await processing_msg.delete()
        await message.reply("❌ FFmpeg не смог обработать это видео.")
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
