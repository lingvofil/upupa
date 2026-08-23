"""Image-to-video via a public Hugging Face ZeroGPU Space.

Primary Space: multimodalart/wan2-1-fast (Wan 2.1 I2V + CausVid LoRA).
The public Space can change or become rate-limited, so callers must keep a
fallback provider and treat every failure here as recoverable.
"""

import asyncio
from io import BytesIO
import logging
import math
from pathlib import Path
import tempfile
from typing import Any

import requests
from gradio_client import Client, handle_file
from PIL import Image

from core.settings import HUGGINGFACE_TOKEN


HF_VIDEO_SPACE_ID = "multimodalart/wan2-1-fast"
HF_VIDEO_DURATION_SECONDS = 3.3
HF_VIDEO_STEPS = 4
HF_VIDEO_TIMEOUT_SECONDS = 210
HF_VIDEO_MAX_BYTES = 50 * 1024 * 1024

# Taken from the public Space defaults. Keeping it locally makes the API call
# independent from UI defaults and avoids accidental text/watermark artifacts.
HF_VIDEO_NEGATIVE_PROMPT = (
    "Bright tones, overexposed, static, blurred details, subtitles, paintings, "
    "worst quality, low quality, JPEG compression residue, ugly, incomplete, "
    "deformed, disfigured, misshapen limbs, fused fingers, still picture, "
    "messy background, watermark, text, signature"
)


def _calculate_wan_dimensions(image_bytes: bytes) -> tuple[int, int]:
    """Match the public Space's aspect-preserving 480p dimension calculation."""
    with Image.open(BytesIO(image_bytes)) as image:
        orig_w, orig_h = image.size

    if orig_w <= 0 or orig_h <= 0:
        return 512, 896

    mod = 32
    max_area = 480.0 * 832.0
    aspect_ratio = orig_h / orig_w
    calc_h = round(math.sqrt(max_area * aspect_ratio))
    calc_w = round(math.sqrt(max_area / aspect_ratio))
    calc_h = max(mod, (calc_h // mod) * mod)
    calc_w = max(mod, (calc_w // mod) * mod)
    height = max(128, min(calc_h, 896))
    width = max(128, min(calc_w, 896))
    return height, width


def _endpoint_has_video(endpoint: dict) -> bool:
    returns = endpoint.get("returns") or []
    return any(str(item.get("component", "")).lower() == "video" for item in returns if isinstance(item, dict))


def _endpoint_has_image_input(endpoint: dict) -> bool:
    parameters = endpoint.get("parameters") or []
    return any(str(item.get("component", "")).lower() == "image" for item in parameters if isinstance(item, dict))


def _discover_generate_endpoint(client: Client) -> tuple[str | None, int | None]:
    """Find the generation endpoint; fall back to the current function-derived name."""
    try:
        info = client.view_api(print_info=False, return_format="dict") or {}
        named = info.get("named_endpoints") or {}

        for api_name, endpoint in named.items():
            low = str(api_name).lower()
            if "generate" in low and "video" in low:
                return str(api_name), None

        for api_name, endpoint in named.items():
            if isinstance(endpoint, dict) and _endpoint_has_video(endpoint) and _endpoint_has_image_input(endpoint):
                return str(api_name), None

        unnamed = info.get("unnamed_endpoints") or {}
        for fn_index, endpoint in unnamed.items():
            if isinstance(endpoint, dict) and _endpoint_has_video(endpoint) and _endpoint_has_image_input(endpoint):
                return None, int(fn_index)
    except Exception as exc:
        logging.warning("HF ZeroGPU API discovery failed: %s", exc)

    # Gradio derives this name from generate_video in the current public Space.
    return "/generate_video", None


def _read_video_result(value: Any) -> bytes | None:
    """Extract downloaded Gradio video bytes from tuple/FileData/path/URL shapes."""
    if value is None:
        return None

    if isinstance(value, (tuple, list)):
        for item in value:
            data = _read_video_result(item)
            if data:
                return data
        return None

    if isinstance(value, dict):
        for key in ("path", "video", "url"):
            if key in value:
                data = _read_video_result(value[key])
                if data:
                    return data
        return None

    if isinstance(value, Path):
        value = str(value)

    if isinstance(value, str):
        path = Path(value)
        if path.is_file():
            data = path.read_bytes()
            return data if 0 < len(data) <= HF_VIDEO_MAX_BYTES else None

        if value.startswith(("https://", "http://")):
            response = requests.get(value, timeout=60)
            response.raise_for_status()
            data = response.content
            return data if 0 < len(data) <= HF_VIDEO_MAX_BYTES else None

    return None


def _classify_failure(exc: Exception) -> str:
    text = str(exc).lower()
    quota_markers = (
        "quota", "rate limit", "rate_limit", "too many requests", "429",
        "zero gpu", "zerogpu", "gpu quota", "exceeded your gpu",
    )
    if any(marker in text for marker in quota_markers):
        return "quota"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "unavailable"


def _generate_sync(image_bytes: bytes, prompt: str) -> tuple[bytes | None, str]:
    height, width = _calculate_wan_dimensions(image_bytes)
    prompt = (prompt or "make this image come alive, cinematic motion, smooth natural animation").strip()[:500]

    with tempfile.TemporaryDirectory(prefix="upupa_hf_video_") as temp_dir:
        image_path = Path(temp_dir) / "input.jpg"
        with Image.open(BytesIO(image_bytes)) as image:
            image.convert("RGB").save(image_path, format="JPEG", quality=95)

        client = None
        try:
            client = Client(
                HF_VIDEO_SPACE_ID,
                token=HUGGINGFACE_TOKEN or None,
                verbose=False,
                download_files=temp_dir,
            )
            api_name, fn_index = _discover_generate_endpoint(client)

            args = (
                handle_file(str(image_path)),
                prompt,
                height,
                width,
                HF_VIDEO_NEGATIVE_PROMPT,
                HF_VIDEO_DURATION_SECONDS,
                1.0,                  # guidance scale
                HF_VIDEO_STEPS,
                42,
                True,                 # randomize seed
            )

            submit_kwargs = {"api_name": api_name} if api_name else {"fn_index": fn_index}
            job = client.submit(*args, **submit_kwargs)
            result = job.result(timeout=HF_VIDEO_TIMEOUT_SECONDS)
            video = _read_video_result(result)
            if video:
                logging.info(
                    "HF ZeroGPU video generated: space=%s endpoint=%s fn_index=%s size=%s",
                    HF_VIDEO_SPACE_ID, api_name, fn_index, len(video),
                )
                return video, "ok"

            logging.warning("HF ZeroGPU returned no readable video: %r", result)
            return None, "unavailable"
        except Exception as exc:
            failure = _classify_failure(exc)
            logging.warning("HF ZeroGPU video failed (%s): %s", failure, exc)
            return None, failure
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


async def generate_hf_zerogpu_video(image_bytes: bytes, prompt: str) -> tuple[bytes | None, str]:
    """Generate image-to-video without blocking the aiogram event loop."""
    return await asyncio.to_thread(_generate_sync, image_bytes, prompt)
