"""Format-preserving wrapper for the legacy ``дисторшн`` pipeline.

The distortion algorithms themselves stay in :mod:`services.distortion`.  This
module fixes the transport/container layer: Telegram metadata is used to keep
the original file extension, distortion is rendered through the legacy MP3/MP4
pipeline, and the result is converted back to the input container when needed.
"""

from __future__ import annotations

import logging
import os
import random
import shutil
from typing import Any

from services import distortion


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac"}

MIME_EXTENSION_MAP = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/aac": ".aac",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "image/gif": ".gif",
}

FORMAT_PRESERVED_MEDIA_TYPES = {
    "audio",
    "voice",
    "video",
    "video_note",
    "animation",
    "video_document",
}


def media_extension(
    media: Any,
    fallback: str,
    *,
    allowed: set[str] | None = None,
) -> str:
    """Return the original extension from Telegram metadata when it is safe."""
    file_name = getattr(media, "file_name", None)
    if file_name:
        ext = os.path.splitext(file_name)[1].lower()
        if ext and (allowed is None or ext in allowed):
            return ext

    mime_type = (getattr(media, "mime_type", None) or "").lower()
    ext = MIME_EXTENSION_MAP.get(mime_type)
    if ext and (allowed is None or ext in allowed):
        return ext
    return fallback


def _audio_codec_args(extension: str) -> list[str]:
    if extension == ".mp3":
        return ["-c:a", "libmp3lame", "-q:a", "4"]
    if extension in {".ogg", ".opus"}:
        return ["-c:a", "libopus", "-b:a", "96k"]
    if extension in {".m4a", ".aac"}:
        return ["-c:a", "aac", "-b:a", "128k"]
    if extension == ".wav":
        return ["-c:a", "pcm_s16le"]
    if extension == ".flac":
        return ["-c:a", "flac"]
    raise ValueError(f"Unsupported audio extension: {extension}")


async def transcode_audio_to_extension(source_path: str, output_path: str) -> bool:
    extension = os.path.splitext(output_path)[1].lower()
    cmd = [
        "ffmpeg", "-y", "-i", source_path,
        "-vn",
        *_audio_codec_args(extension),
        output_path,
    ]
    ok, err = await distortion.run_ffmpeg_command(cmd)
    if not ok:
        logging.error("Could not restore audio container %s: %s", extension, err)
    return ok


async def transcode_video_to_extension(source_path: str, output_path: str) -> bool:
    extension = os.path.splitext(output_path)[1].lower()
    has_audio = await distortion._has_audio_stream(source_path)
    cmd = ["ffmpeg", "-y", "-i", source_path]

    if extension in {".mov", ".mkv"}:
        cmd += ["-c", "copy"]
    elif extension == ".webm":
        cmd += [
            "-c:v", "libvpx-vp9",
            "-crf", "35",
            "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ]
        if has_audio:
            cmd += ["-c:a", "libopus", "-b:a", "96k"]
        else:
            cmd += ["-an"]
    elif extension == ".avi":
        cmd += ["-c:v", "mpeg4", "-q:v", "6"]
        if has_audio:
            cmd += ["-c:a", "libmp3lame", "-q:a", "4"]
        else:
            cmd += ["-an"]
    elif extension == ".m4v":
        # M4V is a video-only container. Preserve the container rather than
        # silently returning MP4 just because the legacy worker renders MP4.
        cmd += ["-an", "-c:v", "mpeg4", "-q:v", "5", "-f", "m4v"]
    elif extension == ".gif":
        cmd += ["-an", "-vf", f"fps={distortion.VIDEO_DISTORTION_FPS}", "-loop", "0"]
    elif extension == ".mp4":
        cmd += ["-c", "copy"]
    else:
        raise ValueError(f"Unsupported video extension: {extension}")

    cmd.append(output_path)
    ok, err = await distortion.run_ffmpeg_command(cmd)
    if not ok:
        logging.error("Could not restore video container %s: %s", extension, err)
    return ok


async def _distort_audio_preserving_format(
    input_path: str,
    output_path: str,
    intensity: int,
) -> bool:
    extension = os.path.splitext(output_path)[1].lower()
    if extension == ".mp3":
        return await distortion.apply_ffmpeg_audio_distortion(input_path, output_path, intensity)

    intermediate_path = f"{input_path}_distorted.mp3"
    if not await distortion.apply_ffmpeg_audio_distortion(input_path, intermediate_path, intensity):
        return False
    return await transcode_audio_to_extension(intermediate_path, output_path)


async def _distort_video_preserving_format(
    input_path: str,
    output_path: str,
    intensity: int,
) -> bool:
    extension = os.path.splitext(output_path)[1].lower()
    render_path = output_path if extension == ".mp4" else f"{input_path}_distorted.mp4"

    success = await distortion.apply_seam_carving_video_distortion(input_path, render_path, intensity)
    if not success:
        success = await distortion.apply_ffmpeg_video_distortion(input_path, render_path, intensity)
    if not success:
        return False
    if render_path == output_path:
        return True
    return await transcode_video_to_extension(render_path, output_path)


async def format_preserving_worker_async(
    bot_token: str,
    chat_id: int,
    media_info: dict,
    intensity: int,
    *,
    distortion_module=distortion,
) -> None:
    """Process regular audio/video while preserving their input container."""
    bot_instance = distortion_module.Bot(token=bot_token)
    media_type = media_info["media_type"]
    input_path = media_info.get("local_path")
    output_path = None

    try:
        if media_type in {"audio", "voice"}:
            info = await distortion_module.get_media_info(input_path)
            if not info or "format" not in info or "duration" not in info["format"]:
                await bot_instance.send_message(
                    chat_id,
                    "Не удалось прочитать информацию о файле. Возможно, он поврежден.",
                )
                return
            duration = float(info["format"]["duration"])
            if duration > distortion_module.MAX_AUDIO_DURATION:
                await bot_instance.send_message(
                    chat_id,
                    f"Слишком длинный аудиофайл ({duration:.1f}с > {distortion_module.MAX_AUDIO_DURATION}с).",
                )
                return

        extension = media_info["ext"]
        output_path = f"{input_path}_out{extension}"

        if media_type in {"audio", "voice"}:
            success = await _distort_audio_preserving_format(input_path, output_path, intensity)
        else:
            success = await _distort_video_preserving_format(input_path, output_path, intensity)

        if not success or not output_path or not os.path.exists(output_path):
            await bot_instance.send_message(chat_id, "Что-то пошло не так во время искажения.")
            return

        file_name = media_info.get("output_file_name")
        file_to_send = distortion_module.FSInputFile(output_path, filename=file_name)
        caption = "🌀 твоя хуйня готова"

        if media_type == "audio":
            await bot_instance.send_audio(chat_id, file_to_send, caption=caption)
        elif media_type == "voice":
            await bot_instance.send_voice(chat_id, file_to_send, caption=caption)
        elif media_type == "animation":
            await bot_instance.send_animation(chat_id, file_to_send, caption=caption)
        elif media_type == "video_note":
            await bot_instance.send_video_note(chat_id, file_to_send)
        elif media_type == "video_document":
            await bot_instance.send_document(chat_id, file_to_send, caption=caption)
        else:
            await bot_instance.send_video(chat_id, file_to_send, caption=caption)
    except Exception as exc:
        logging.error("Format-preserving distortion worker failed: %s", exc, exc_info=True)
        try:
            await bot_instance.send_message(chat_id, "Произошла внутренняя ошибка при обработке.")
        except Exception as send_exc:
            logging.error("Could not send distortion error: %s", send_exc)
    finally:
        input_dir = os.path.dirname(input_path) if input_path else ""
        if input_dir and os.path.basename(input_dir).startswith("temp_worker_"):
            shutil.rmtree(input_dir, ignore_errors=True)
        await bot_instance.session.close()


def _distorted_output_name(file_name: str | None, extension: str) -> str | None:
    if not file_name:
        return None
    stem = os.path.splitext(os.path.basename(file_name))[0]
    return f"{stem}_distorted{extension}"


async def handle_format_preserving_distortion_request(
    message: Any,
    *,
    distortion_module=distortion,
) -> None:
    """Download media using its real input extension before starting distortion."""
    try:
        target_message = message.reply_to_message or message
        text_for_parsing = message.text if message.text else message.caption
        intensity = distortion_module.parse_intensity_from_text(text_for_parsing)

        media_info: dict[str, Any] = {}
        file_to_download = None

        if target_message.photo:
            media_info = {"media_type": "photo", "ext": ".jpg"}
            file_to_download = target_message.photo[-1]
        elif target_message.sticker:
            if target_message.sticker.is_animated:
                media_info = {"media_type": "sticker_tgs", "ext": ".tgs"}
            elif target_message.sticker.is_video:
                media_info = {"media_type": "sticker_video", "ext": ".webm"}
            else:
                media_info = {"media_type": "sticker_static", "ext": ".webp"}
            file_to_download = target_message.sticker
        elif target_message.video:
            ext = media_extension(
                target_message.video,
                ".mp4",
                allowed=distortion_module.SUPPORTED_VIDEO_EXTENSIONS,
            )
            media_info = {"media_type": "video", "ext": ext}
            file_to_download = target_message.video
        elif getattr(target_message, "video_note", None):
            media_info = {"media_type": "video_note", "ext": ".mp4"}
            file_to_download = target_message.video_note
        elif target_message.animation:
            ext = media_extension(
                target_message.animation,
                ".mp4",
                allowed=distortion_module.SUPPORTED_VIDEO_EXTENSIONS,
            )
            media_info = {"media_type": "animation", "ext": ext}
            file_to_download = target_message.animation
        elif target_message.document and distortion_module.is_video_document(target_message.document):
            ext = media_extension(
                target_message.document,
                ".mp4",
                allowed=distortion_module.SUPPORTED_VIDEO_EXTENSIONS,
            )
            media_info = {
                "media_type": "video_document",
                "ext": ext,
                "output_file_name": _distorted_output_name(target_message.document.file_name, ext),
            }
            file_to_download = target_message.document
        elif target_message.audio:
            ext = media_extension(target_message.audio, ".mp3", allowed=AUDIO_EXTENSIONS)
            media_info = {
                "media_type": "audio",
                "ext": ext,
                "output_file_name": _distorted_output_name(target_message.audio.file_name, ext),
            }
            file_to_download = target_message.audio
        elif target_message.voice:
            ext = media_extension(target_message.voice, ".ogg", allowed=AUDIO_EXTENSIONS)
            media_info = {"media_type": "voice", "ext": ext}
            file_to_download = target_message.voice
        elif target_message.text:
            media_info = {"media_type": "text", "text": target_message.text}

        if not media_info:
            await message.answer("Не нашел, что искажать.")
            return

        if file_to_download:
            temp_dir = f"temp_worker_{random.randint(1000, 9999)}"
            os.makedirs(temp_dir, exist_ok=True)
            local_path = os.path.join(temp_dir, f"input{media_info['ext']}")
            if not await distortion_module.download_file(file_to_download.file_id, local_path):
                await message.answer("Не смог скачать файл.")
                shutil.rmtree(temp_dir, ignore_errors=True)
                return
            media_info["local_path"] = local_path

        await message.answer("🌀 ща, сука...")
        await distortion_module.distortion_worker_async(
            distortion_module.main_bot_instance.token,
            message.chat.id,
            media_info,
            intensity,
        )
    except Exception as exc:
        logging.error("Format-preserving distortion handler failed: %s", exc, exc_info=True)
        await message.answer("Не удалось запустить обработку.")


def install_into_distortion(distortion_module: Any = distortion) -> None:
    """Patch regular media handling while leaving sticker interception composable."""
    if getattr(distortion_module, "_format_preservation_installed", False):
        return

    original_worker = distortion_module.distortion_worker_async

    async def patched_worker(bot_token: str, chat_id: int, media_info: dict, intensity: int):
        if media_info.get("media_type") in FORMAT_PRESERVED_MEDIA_TYPES:
            return await format_preserving_worker_async(
                bot_token,
                chat_id,
                media_info,
                intensity,
                distortion_module=distortion_module,
            )
        return await original_worker(bot_token, chat_id, media_info, intensity)

    async def patched_handler(message: Any):
        return await handle_format_preserving_distortion_request(
            message,
            distortion_module=distortion_module,
        )

    distortion_module.distortion_worker_async = patched_worker
    distortion_module.handle_distortion_request = patched_handler
    distortion_module._format_preservation_installed = True
