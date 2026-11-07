"""Kandinsky 6.0 video (text/image-to-video) via public Hugging Face ZeroGPU Spaces.

Официальный API Сбера видео не отдаёт:
- GigaChat API: встроенной функции text2video нет (модель сама отвечает, что
  умеет только картинки; function_call={"name": "text2video"} -> HTTP 422);
- FusionBrain (api-key.fusionbrain.ai): только TEXT2IMAGE, и хост сейчас
  не отвечает (connect timeout и с VPS, и из Москвы).
Зато веса Kandinsky 6.0 Video открыты (MIT), и есть живые ZeroGPU Spaces.

Ограничение: квота ZeroGPU у бесплатного HF-токена — несколько минут GPU в
сутки. Lite-Space запрашивает 135 c квоты (90 c × 1.5 за размер large), хотя
сам ролик идёт ~75 c GPU; Pro-Space запрашивает 300 c и на бесплатной квоте
не проходит в принципе. Поэтому вызывающий код обязан держать фолбэк
(Pollinations).

Wan 2.1 (multimodalart/wan2-1-fast) из цепочки убран в ноябре 2026: Space
за пять недель не выдал ни одного видео (каждый вызов — AppError
'RuntimeError'), а неудачные вызовы всё равно съедали квоту ZeroGPU.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import logging
from pathlib import Path
import tempfile
from typing import Any

import requests
from gradio_client import Client, handle_file
from PIL import Image

from core.settings import HUGGINGFACE_TOKEN


# Порядок важен: Lite укладывается в бесплатную квоту, Pro — только с HF PRO.
# kwargs — точные имена параметров эндпоинта /generate (проверено view_api).
KANDINSKY_VIDEO_SPACES: list[tuple[str, dict]] = [
    ("hugging-apps/kandinsky-6-lite-distill-5s", {"expand_prompt": False}),
    ("kandinskylab/Kandinsky-6.0-Pro-distill-5s", {"use_beautifier": True}),
]
KANDINSKY_VIDEO_API_NAME = "/generate"
KANDINSKY_VIDEO_TIMEOUT_SECONDS = 240
KANDINSKY_VIDEO_MAX_BYTES = 50 * 1024 * 1024

DEFAULT_ANIMATE_PROMPT = "make this image come alive, cinematic motion, smooth natural animation"


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
            return data if 0 < len(data) <= KANDINSKY_VIDEO_MAX_BYTES else None

        if value.startswith(("https://", "http://")):
            response = requests.get(value, timeout=60)
            response.raise_for_status()
            data = response.content
            return data if 0 < len(data) <= KANDINSKY_VIDEO_MAX_BYTES else None

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


def _prepare_image(image_bytes: bytes, temp_dir: str) -> str:
    path = Path(temp_dir) / "input.jpg"
    with Image.open(BytesIO(image_bytes)) as image:
        image.convert("RGB").save(path, format="JPEG", quality=95)
    return str(path)


def _generate_on_space(space_id: str, extra: dict, prompt: str, image_path: str | None, temp_dir: str) -> tuple[bytes | None, str]:
    client = None
    try:
        client = Client(space_id, token=HUGGINGFACE_TOKEN or None, verbose=False, download_files=temp_dir)
        kwargs = dict(extra, prompt=prompt, seed=42)
        if image_path:
            kwargs["image"] = handle_file(image_path)
        job = client.submit(api_name=KANDINSKY_VIDEO_API_NAME, **kwargs)
        result = job.result(timeout=KANDINSKY_VIDEO_TIMEOUT_SECONDS)
        video = _read_video_result(result)
        if video:
            logging.info("Kandinsky video generated: space=%s i2v=%s size=%s", space_id, bool(image_path), len(video))
            return video, "ok"
        logging.warning("Kandinsky video space=%s returned no readable video: %r", space_id, result)
        return None, "unavailable"
    except Exception as exc:
        failure = _classify_failure(exc)
        logging.warning("Kandinsky video space=%s failed (%s): %s", space_id, failure, exc)
        return None, failure
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _generate_sync(image_bytes: bytes | None, prompt: str) -> tuple[bytes | None, str]:
    prompt = (prompt or DEFAULT_ANIMATE_PROMPT).strip()[:500]
    status = "unavailable"
    with tempfile.TemporaryDirectory(prefix="upupa_kandinsky_video_") as temp_dir:
        image_path = _prepare_image(image_bytes, temp_dir) if image_bytes else None
        for space_id, extra in KANDINSKY_VIDEO_SPACES:
            video, status = _generate_on_space(space_id, extra, prompt, image_path, temp_dir)
            if video:
                return video, "ok"
            # Квота общая на токен: если Lite упёрся в неё, Pro (300 c) тем более.
            if status == "quota":
                break
    return None, status


async def generate_kandinsky_video(image_bytes: bytes | None, prompt: str) -> tuple[bytes | None, str]:
    """Kandinsky 6.0: image-to-video, если передан кадр, иначе text-to-video.

    Возвращает (mp4, "ok") или (None, "quota" | "timeout" | "unavailable").
    """
    return await asyncio.to_thread(_generate_sync, image_bytes, prompt)
