"""Alpha-safe seam carving for Telegram sticker distortion.

The sticker pipeline must transform colour and alpha with the *same* seam map.
Running seam carving independently for RGB and alpha can expose the black RGB
stored under transparent pixels, producing dark silhouettes/halos.  This module
keeps RGBA spatially aligned and performs resampling in premultiplied-alpha
space before restoring straight RGBA for WEBP/VP9 encoding.
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

from services import distortion
from services import distortion_stickers as stickers

try:
    # seam-carving is pinned to 1.1.0 in requirements.txt.  Its public resize()
    # does not expose the seam map, while _get_seams() lets us apply one exact
    # map to all four premultiplied RGBA channels.
    from seam_carving.carve import _get_seams as _sc_get_seams
    SEAM_MAP_AVAILABLE = True
except (ImportError, AttributeError):  # pragma: no cover - dependency is pinned
    _sc_get_seams = None
    SEAM_MAP_AVAILABLE = False


_LUMA = np.array([0.2125, 0.7154, 0.0721], dtype=np.float32)
_ALPHA_EPSILON = 1.0 / 255.0


def _premultiply_rgba(image: Image.Image) -> np.ndarray:
    """Return float32 RGBA where RGB is premultiplied by alpha."""
    rgba = np.array(image.convert("RGBA"), dtype=np.float32)
    alpha = rgba[:, :, 3:4] / 255.0
    rgba[:, :, :3] *= alpha
    return rgba


def _seam_energy_source(premultiplied_rgba: np.ndarray) -> np.ndarray:
    """Build a grayscale source for seam selection.

    Premultiplied colour prevents hidden RGB from influencing energy in fully
    transparent regions.  A small alpha term preserves useful contour energy
    even for dark/black sticker artwork.
    """
    rgb_luma = premultiplied_rgba[:, :, :3] @ _LUMA
    alpha = premultiplied_rgba[:, :, 3]
    return (rgb_luma + 0.35 * alpha).astype(np.float32)


def _remove_vertical_seams_same_map(
    premultiplied_rgba: np.ndarray,
    target_width: int,
) -> np.ndarray:
    """Remove seams once and apply the exact boolean map to all RGBA channels."""
    height, width, channels = premultiplied_rgba.shape
    if channels != 4:
        raise ValueError(f"expected premultiplied RGBA, got shape {premultiplied_rgba.shape}")
    if target_width <= 0 or target_width > width:
        raise ValueError(f"invalid target width {target_width} for source width {width}")
    if target_width == width:
        return premultiplied_rgba
    if not SEAM_MAP_AVAILABLE or _sc_get_seams is None:
        raise RuntimeError("seam-carving 1.1.0 seam-map internals are unavailable")

    num_seams = width - target_width
    gray = _seam_energy_source(premultiplied_rgba)
    seams = _sc_get_seams(gray, num_seams, "backward", None)
    if seams.shape != (height, width):
        raise RuntimeError(f"unexpected seam map shape {seams.shape}, expected {(height, width)}")

    keep = ~seams
    return premultiplied_rgba[keep].reshape((height, target_width, 4))


def _carve_premultiplied_rgba(
    premultiplied_rgba: np.ndarray,
    new_width: int,
    new_height: int,
) -> np.ndarray:
    """Width-first seam carving with one shared map for colour and alpha."""
    carved = _remove_vertical_seams_same_map(premultiplied_rgba, new_width)
    if carved.shape[0] != new_height:
        transposed = carved.transpose((1, 0, 2))
        transposed = _remove_vertical_seams_same_map(transposed, new_height)
        carved = transposed.transpose((1, 0, 2))
    return carved


def _resize_float_channel(channel: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Lanczos resize one float channel without converting through uint8."""
    image = Image.fromarray(channel.astype(np.float32))
    resized = image.resize(size, Image.LANCZOS)
    return np.asarray(resized, dtype=np.float32)


def _restore_straight_rgba(
    premultiplied_rgba: np.ndarray,
    original_size: tuple[int, int],
) -> Image.Image:
    """Resize back in premultiplied space, then unpremultiply exactly once."""
    resized_channels = [
        _resize_float_channel(premultiplied_rgba[:, :, index], original_size)
        for index in range(4)
    ]
    resized = np.stack(resized_channels, axis=2)

    alpha = np.clip(resized[:, :, 3:4], 0.0, 255.0)
    alpha_norm = alpha / 255.0
    premult_rgb = np.clip(resized[:, :, :3], 0.0, 255.0)

    rgb = np.zeros_like(premult_rgb, dtype=np.float32)
    valid_alpha = alpha_norm > _ALPHA_EPSILON
    np.divide(
        premult_rgb,
        np.where(valid_alpha, alpha_norm, 1.0),
        out=rgb,
        where=np.broadcast_to(valid_alpha, premult_rgb.shape),
    )
    rgb = np.clip(rgb, 0.0, 255.0)

    # Fully/near-transparent pixels must not retain arbitrary hidden colour.
    transparent = alpha[:, :, 0] <= 1.0
    rgb[transparent] = 0.0

    rgba = np.concatenate((rgb, alpha), axis=2)
    return Image.fromarray(np.rint(rgba).astype(np.uint8), "RGBA")


def _seam_resize_rgba_same_map(
    rgba: Image.Image,
    new_width: int,
    new_height: int,
) -> Image.Image:
    """Content-aware resize without ever desynchronising RGB and alpha."""
    original_size = rgba.size
    premultiplied = _premultiply_rgba(rgba)
    carved = _carve_premultiplied_rgba(premultiplied, new_width, new_height)
    return _restore_straight_rgba(carved, original_size)


def _distort_rgba_frame_task(frame_path: str, distort_percent: int) -> None:
    """Process-pool worker for a single PNG frame with alpha-safe seam carving."""
    with Image.open(frame_path) as source:
        rgba = source.convert("RGBA")
    width, height = rgba.size
    distort_percent = max(5, min(distort_percent, 90))
    new_width = max(int(width * (100 - distort_percent) / 100), 20)
    new_height = max(int(height * (100 - distort_percent) / 100), 20)
    distorted = _seam_resize_rgba_same_map(rgba, new_width, new_height)
    distorted.save(frame_path, "PNG")


async def apply_rgba_static_sticker_distortion(
    input_path: str,
    output_path: str,
    intensity: int,
) -> bool:
    """Distort static WEBP while keeping transparent RGB/alpha aligned."""
    if not SEAM_MAP_AVAILABLE:
        return False
    try:
        with Image.open(input_path) as source:
            rgba = source.convert("RGBA")
        if rgba.width < 20 or rgba.height < 20:
            return False

        distort_percent = max(0, min(intensity, 95))
        new_width = max(int(rgba.width * (100 - distort_percent) / 100), 20)
        new_height = max(int(rgba.height * (100 - distort_percent) / 100), 20)
        distorted = await asyncio.to_thread(
            _seam_resize_rgba_same_map,
            rgba,
            new_width,
            new_height,
        )
        return await asyncio.to_thread(stickers._save_webp_with_limit, distorted, output_path)
    except Exception as exc:
        logging.error("Alpha-safe static sticker distortion failed: %s", exc, exc_info=True)
        return False


async def apply_rgba_video_sticker_distortion(
    input_path: str,
    output_path: str,
    intensity: int,
) -> bool:
    """Distort RGBA frames with one seam map, then encode Telegram VP9 alpha."""
    if not SEAM_MAP_AVAILABLE:
        return False

    work_dir = f"{input_path}_alpha_frames_{random.randint(1000, 9999)}"
    os.makedirs(work_dir, exist_ok=True)
    frames_pattern = os.path.join(work_dir, "frame_%05d.png")
    try:
        input_args = await stickers._alpha_preserving_input_args(input_path)
        extract_cmd = [
            "ffmpeg", "-y", *input_args,
            "-an",
            "-t", str(stickers.TELEGRAM_VIDEO_STICKER_MAX_SECONDS),
            "-vf", (
                f"fps={stickers.VIDEO_STICKER_WORK_FPS},"
                "scale=512:512:force_original_aspect_ratio=decrease:flags=lanczos,"
                "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=black@0,format=rgba"
            ),
            frames_pattern,
        ]
        ok, err = await distortion.run_ffmpeg_command(extract_cmd)
        if not ok:
            logging.warning("Could not extract alpha-safe sticker frames: %s", err)
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

        return await stickers._encode_video_sticker_attempts(
            ["-framerate", str(stickers.VIDEO_STICKER_WORK_FPS), "-i", frames_pattern],
            output_path,
        )
    except Exception as exc:
        logging.error("Alpha-safe video sticker distortion failed: %s", exc, exc_info=True)
        return False
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def install_alpha_safe_rgba(sticker_module: Any = stickers) -> None:
    """Replace only the two RGBA distortion functions used by sticker workers."""
    if getattr(sticker_module, "_alpha_safe_rgba_installed", False):
        return
    sticker_module.apply_rgba_static_sticker_distortion = apply_rgba_static_sticker_distortion
    sticker_module.apply_rgba_video_sticker_distortion = apply_rgba_video_sticker_distortion
    sticker_module._alpha_safe_rgba_installed = True
