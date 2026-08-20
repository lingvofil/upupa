"""Sticker-preserving output pipeline for the ``дисторшн`` command.

The legacy distortion worker intentionally remains responsible for text, photo,
audio and regular video.  This module patches only sticker media types so a
sticker goes back to Telegram as a sticker instead of being downgraded to a
photo/video message.
"""

from __future__ import annotations

import asyncio
import glob
import logging
import math
import os
import random
import shutil
from typing import Any

import numpy as np
from PIL import Image

try:
    import seam_carving
    SEAM_CARVING_AVAILABLE = True
except ImportError:  # pragma: no cover - mirrors services.distortion
    seam_carving = None
    SEAM_CARVING_AVAILABLE = False

from services import distortion


STICKER_MEDIA_TYPES = {"sticker_static", "sticker_video", "sticker_tgs"}
TELEGRAM_STICKER_SIDE = 512
TELEGRAM_STATIC_STICKER_MAX_BYTES = 512 * 1024
TELEGRAM_VIDEO_STICKER_MAX_BYTES = 256 * 1024
TELEGRAM_VIDEO_STICKER_MAX_SECONDS = 3.0
TELEGRAM_VIDEO_STICKER_MAX_FPS = 30
VIDEO_STICKER_WORK_FPS = 12


def _fit_rgba_to_sticker_canvas(image: Image.Image) -> Image.Image:
    """Fit an RGBA image on a transparent 512x512 Telegram sticker canvas."""
    rgba = image.convert("RGBA")
    rgba.thumbnail((TELEGRAM_STICKER_SIDE, TELEGRAM_STICKER_SIDE), Image.LANCZOS)
    canvas = Image.new("RGBA", (TELEGRAM_STICKER_SIDE, TELEGRAM_STICKER_SIDE), (0, 0, 0, 0))
    x = (TELEGRAM_STICKER_SIDE - rgba.width) // 2
    y = (TELEGRAM_STICKER_SIDE - rgba.height) // 2
    canvas.alpha_composite(rgba, dest=(x, y))
    return canvas


def _save_webp_with_limit(image: Image.Image, output_path: str) -> bool:
    """Save RGBA WEBP while keeping the file inside Telegram's static limit."""
    sticker = _fit_rgba_to_sticker_canvas(image)
    attempts = [
        {"lossless": True, "method": 6},
        {"lossless": False, "quality": 95, "method": 6},
        {"lossless": False, "quality": 88, "method": 6},
        {"lossless": False, "quality": 78, "method": 6},
        {"lossless": False, "quality": 68, "method": 6},
    ]
    for options in attempts:
        sticker.save(output_path, "WEBP", **options)
        if os.path.getsize(output_path) <= TELEGRAM_STATIC_STICKER_MAX_BYTES:
            return True
    logging.error("Distorted WEBP sticker is still larger than Telegram limit")
    return False


def _seam_resize_rgba(rgba: Image.Image, new_w: int, new_h: int) -> Image.Image:
    """Apply the same seam-carving idea to RGB and alpha and merge them back."""
    rgb_np = np.array(rgba.convert("RGB"))
    alpha_np = np.array(rgba.getchannel("A"))
    # seam_carving is most reliable with three-channel arrays; repeat alpha.
    alpha_rgb = np.repeat(alpha_np[:, :, None], 3, axis=2)

    dst_rgb = seam_carving.resize(
        rgb_np, (new_w, new_h), energy_mode="backward", order="width-first"
    )
    dst_alpha_rgb = seam_carving.resize(
        alpha_rgb, (new_w, new_h), energy_mode="backward", order="width-first"
    )

    rgb_img = Image.fromarray(dst_rgb).resize(rgba.size, Image.LANCZOS)
    alpha_img = Image.fromarray(dst_alpha_rgb[:, :, 0]).resize(rgba.size, Image.LANCZOS)
    result = rgb_img.convert("RGBA")
    result.putalpha(alpha_img)
    return result


async def apply_rgba_static_sticker_distortion(
    input_path: str,
    output_path: str,
    intensity: int,
) -> bool:
    """Seam-carve a WEBP sticker without flattening its alpha channel."""
    if not SEAM_CARVING_AVAILABLE:
        return False
    try:
        with Image.open(input_path) as source:
            rgba = source.convert("RGBA")
        if rgba.width < 20 or rgba.height < 20:
            return False

        distort_percent = max(0, min(intensity, 95))
        new_w = max(int(rgba.width * (100 - distort_percent) / 100), 20)
        new_h = max(int(rgba.height * (100 - distort_percent) / 100), 20)
        distorted = await asyncio.to_thread(_seam_resize_rgba, rgba, new_w, new_h)
        return await asyncio.to_thread(_save_webp_with_limit, distorted, output_path)
    except Exception as exc:
        logging.error("Static sticker distortion failed: %s", exc, exc_info=True)
        return False


def _distort_rgba_frame_task(frame_path: str, distort_percent: int) -> None:
    """CPU worker used by the video-sticker frame pipeline."""
    with Image.open(frame_path) as source:
        rgba = source.convert("RGBA")
    w, h = rgba.size
    distort_percent = max(5, min(distort_percent, 90))
    new_w = max(int(w * (100 - distort_percent) / 100), 20)
    new_h = max(int(h * (100 - distort_percent) / 100), 20)
    distorted = _seam_resize_rgba(rgba, new_w, new_h)
    distorted.save(frame_path, "PNG")


def _parse_fps(value: str | None) -> float:
    if not value or value == "0/0":
        return 0.0
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_f = float(denominator)
            return float(numerator) / denominator_f if denominator_f else 0.0
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


async def validate_telegram_video_sticker(output_path: str) -> bool:
    """Verify the hard format limits required for a Telegram video sticker."""
    if not os.path.exists(output_path):
        return False
    if os.path.getsize(output_path) > TELEGRAM_VIDEO_STICKER_MAX_BYTES:
        return False

    info = await distortion.get_media_info(output_path)
    if not info:
        return False
    streams = info.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video or video.get("codec_name") != "vp9":
        return False
    if any(stream.get("codec_type") == "audio" for stream in streams):
        return False

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width > TELEGRAM_STICKER_SIDE or height > TELEGRAM_STICKER_SIDE:
        return False
    if width != TELEGRAM_STICKER_SIDE and height != TELEGRAM_STICKER_SIDE:
        return False

    fps = _parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    if fps > TELEGRAM_VIDEO_STICKER_MAX_FPS + 0.01:
        return False

    raw_duration = info.get("format", {}).get("duration") or video.get("duration")
    try:
        if raw_duration and float(raw_duration) > TELEGRAM_VIDEO_STICKER_MAX_SECONDS + 0.05:
            return False
    except (TypeError, ValueError):
        return False
    return True


def _telegram_video_filter(fps: int, *, extra_filter: str | None = None) -> str:
    filters = []
    if extra_filter:
        filters.append(extra_filter)
    filters.extend(
        [
            f"fps={fps}",
            "scale=512:512:force_original_aspect_ratio=decrease:flags=lanczos",
            "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=black@0",
            "format=yuva420p",
        ]
    )
    return ",".join(filters)


async def _encode_video_sticker_attempts(
    input_args: list[str],
    output_path: str,
    *,
    extra_filter: str | None = None,
) -> bool:
    """Encode and progressively compress until the 256 KiB Telegram cap is met."""
    # High CRF / lower FPS attempts are intentional: the hard 256 KiB cap is
    # more important than preserving every frame of a three-second reaction.
    attempts = [(12, 38), (12, 46), (10, 52), (8, 58), (6, 63)]
    for fps, crf in attempts:
        if os.path.exists(output_path):
            os.remove(output_path)
        cmd = [
            "ffmpeg", "-y",
            *input_args,
            "-an",
            "-t", str(TELEGRAM_VIDEO_STICKER_MAX_SECONDS),
            "-vf", _telegram_video_filter(fps, extra_filter=extra_filter),
            "-c:v", "libvpx-vp9",
            "-b:v", "0",
            "-crf", str(crf),
            "-deadline", "good",
            "-cpu-used", "4",
            "-row-mt", "1",
            "-pix_fmt", "yuva420p",
            "-metadata:s:v:0", "alpha_mode=1",
            output_path,
        ]
        ok, err = await distortion.run_ffmpeg_command(cmd)
        if not ok:
            logging.warning("VP9 sticker encode failed (fps=%s crf=%s): %s", fps, crf, err)
            continue
        if await validate_telegram_video_sticker(output_path):
            return True
        size = os.path.getsize(output_path) if os.path.exists(output_path) else -1
        logging.info(
            "VP9 sticker attempt did not fit constraints: fps=%s crf=%s size=%s",
            fps,
            crf,
            size,
        )
    return False


async def encode_media_as_video_sticker(
    input_path: str,
    output_path: str,
    *,
    extra_filter: str | None = None,
) -> bool:
    return await _encode_video_sticker_attempts(
        ["-i", input_path],
        output_path,
        extra_filter=extra_filter,
    )


async def apply_rgba_video_sticker_distortion(
    input_path: str,
    output_path: str,
    intensity: int,
) -> bool:
    """Seam-carve up to three seconds and encode directly to VP9 WEBM sticker."""
    if not SEAM_CARVING_AVAILABLE:
        return False

    work_dir = f"{input_path}_sticker_frames_{random.randint(1000, 9999)}"
    os.makedirs(work_dir, exist_ok=True)
    frames_pattern = os.path.join(work_dir, "frame_%05d.png")
    try:
        extract_cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-an",
            "-t", str(TELEGRAM_VIDEO_STICKER_MAX_SECONDS),
            "-vf", (
                f"fps={VIDEO_STICKER_WORK_FPS},"
                "scale=512:512:force_original_aspect_ratio=decrease:flags=lanczos,"
                "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=black@0,format=rgba"
            ),
            frames_pattern,
        ]
        ok, err = await distortion.run_ffmpeg_command(extract_cmd)
        if not ok:
            logging.warning("Could not extract sticker frames: %s", err)
            return False

        frames = sorted(glob.glob(os.path.join(work_dir, "frame_*.png")))
        if not frames:
            return False

        base_percent = max(5, min(intensity, 90))
        loop = asyncio.get_running_loop()
        tasks = []
        for index, frame_path in enumerate(frames):
            wobble = 12 * math.sin(index / 5.0)
            frame_percent = int(base_percent + wobble)
            tasks.append(
                loop.run_in_executor(
                    distortion._frame_distortion_pool,
                    _distort_rgba_frame_task,
                    frame_path,
                    frame_percent,
                )
            )
        await asyncio.gather(*tasks)

        return await _encode_video_sticker_attempts(
            ["-framerate", str(VIDEO_STICKER_WORK_FPS), "-i", frames_pattern],
            output_path,
        )
    except Exception as exc:
        logging.error("Video sticker seam-carving failed: %s", exc, exc_info=True)
        return False
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def create_distorted_video_sticker(
    input_path: str,
    output_path: str,
    intensity: int,
) -> bool:
    if await apply_rgba_video_sticker_distortion(input_path, output_path, intensity):
        return True

    # If seam carving or alpha-frame extraction fails, retain distortion rather
    # than silently returning the original sticker.  This fallback is visual
    # only and the final encoder still enforces VP9/no-audio/size/duration.
    safe_intensity = max(0, min(intensity, 80))
    visual_fallback = (
        f"noise=alls={int(distortion.map_intensity(safe_intensity, 12, 55))}:allf=t+u,"
        "eq=contrast=1.35:saturation=1.6"
    )
    return await encode_media_as_video_sticker(
        input_path,
        output_path,
        extra_filter=visual_fallback,
    )


async def distortion_sticker_worker_async(
    bot_token: str,
    chat_id: int,
    media_info: dict,
    intensity: int,
    *,
    distortion_module=distortion,
) -> None:
    """Worker for static, video and TGS stickers; always replies via send_sticker."""
    bot_instance = distortion_module.Bot(token=bot_token)
    media_type = media_info["media_type"]
    input_path = media_info.get("local_path")
    output_path = None
    converted_path = None
    generated_paths: list[str] = []

    try:
        success = False
        if media_type == "sticker_static":
            output_path = f"{input_path}_out.webp"
            generated_paths.append(output_path)
            success = await apply_rgba_static_sticker_distortion(input_path, output_path, intensity)

        elif media_type == "sticker_video":
            output_path = f"{input_path}_out.webm"
            generated_paths.append(output_path)
            success = await create_distorted_video_sticker(input_path, output_path, intensity)

        elif media_type == "sticker_tgs":
            converted_path = f"{input_path}_converted.webm"
            generated_paths.append(converted_path)
            converted = await distortion_module.convert_tgs_to_webm(input_path, converted_path)
            if not converted:
                await bot_instance.send_message(chat_id, "❌ Не удалось конвертировать TGS в видео.")
                return
            output_path = f"{input_path}_out.webm"
            generated_paths.append(output_path)
            success = await create_distorted_video_sticker(converted_path, output_path, intensity)

        if success and output_path and os.path.exists(output_path):
            await bot_instance.send_sticker(chat_id, distortion_module.FSInputFile(output_path))
        else:
            await bot_instance.send_message(chat_id, "Что-то пошло не так во время искажения стикера.")
    except Exception as exc:
        logging.error("Sticker distortion worker failed: %s", exc, exc_info=True)
        try:
            await bot_instance.send_message(chat_id, "Произошла внутренняя ошибка при обработке стикера.")
        except Exception as send_exc:
            logging.error("Could not send sticker distortion error: %s", send_exc)
    finally:
        input_dir = os.path.dirname(input_path) if input_path else ""
        if input_dir and os.path.basename(input_dir).startswith("temp_worker_"):
            shutil.rmtree(input_dir, ignore_errors=True)
        else:
            for path in generated_paths:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        await bot_instance.session.close()


def install_into_distortion(distortion_module: Any = distortion) -> None:
    """Patch only sticker media types, leaving the legacy worker untouched otherwise."""
    if getattr(distortion_module, "_sticker_output_installed", False):
        return

    original_worker = distortion_module.distortion_worker_async

    async def patched_worker(bot_token: str, chat_id: int, media_info: dict, intensity: int):
        if media_info.get("media_type") in STICKER_MEDIA_TYPES:
            return await distortion_sticker_worker_async(
                bot_token,
                chat_id,
                media_info,
                intensity,
                distortion_module=distortion_module,
            )
        return await original_worker(bot_token, chat_id, media_info, intensity)

    distortion_module.distortion_worker_async = patched_worker
    distortion_module._sticker_output_installed = True
