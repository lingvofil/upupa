"""Admission control for media providers that bypass LazyResource.

These adapters keep legacy image/video modules intact while routing expensive
provider work through the same process-wide AI governor as text generation.
Every wrapped provider also keeps (or gains) its own transport/job timeout so a
caller deadline cannot leave an unbounded remote wait behind it.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import logging
from pathlib import Path
import tempfile
from typing import Any, Awaitable, Callable

import requests
from gradio_client import Client, handle_file
from PIL import Image

from core.settings import (
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_API_TOKEN,
    HUGGINGFACE_TOKEN,
)
from infrastructure.ai.execution import run_ai_provider_call


POLLINATIONS_IMAGE_TIMEOUT_SECONDS = 120
HF_IMAGE_TIMEOUT_SECONDS = 80
CLOUDFLARE_IMAGE_TIMEOUT_SECONDS = 80
GRADIO_IMG2IMG_JOB_TIMEOUT_SECONDS = 180
GRADIO_IMG2IMG_REQUEST_TIMEOUT_SECONDS = 200
HF_VIDEO_INNER_TIMEOUT_SECONDS = 220
HF_VIDEO_REQUEST_TIMEOUT_SECONDS = 230
POLLINATIONS_VIDEO_INNER_TIMEOUT_SECONDS = 430
POLLINATIONS_VIDEO_REQUEST_TIMEOUT_SECONDS = 440


async def _run_governed_sync(
    operation: str,
    func: Callable[[], Any],
    *,
    timeout_seconds: float,
) -> Any:
    return await asyncio.to_thread(
        lambda: run_ai_provider_call(
            operation,
            func,
            timeout_seconds=timeout_seconds,
        )
    )


def _wrap_async_provider(
    original: Callable[..., Awaitable[Any]],
    operation: str,
    *,
    request_timeout_seconds: float,
    inner_timeout_seconds: float | None = None,
):
    if getattr(original, "_upupa_ai_governed", False):
        return original

    async def wrapped(*args, **kwargs):
        def invoke():
            async def execute():
                call = original(*args, **kwargs)
                if inner_timeout_seconds is None:
                    return await call
                return await asyncio.wait_for(call, timeout=inner_timeout_seconds)

            return asyncio.run(execute())

        return await _run_governed_sync(
            operation,
            invoke,
            timeout_seconds=request_timeout_seconds,
        )

    wrapped.__name__ = getattr(original, "__name__", "governed_provider")
    wrapped.__doc__ = getattr(original, "__doc__", None)
    wrapped._upupa_ai_governed = True
    return wrapped


def _gradio_img2img_sync(image_bytes: bytes, prompt: str) -> bytes | None:
    client = None
    with tempfile.TemporaryDirectory(prefix="upupa_gradio_img2img_") as temp_dir:
        source_path = Path(temp_dir) / "source.jpg"
        source_path.write_bytes(image_bytes)
        try:
            client = Client(
                "victor/dlss-5-anything",
                token=HUGGINGFACE_TOKEN,
                verbose=False,
                download_files=temp_dir,
            )
            job = client.submit(
                image=handle_file(str(source_path)),
                prompt=prompt,
                seed=0,
                randomize_seed=True,
                num_inference_steps=20,
                api_name="/on_generate",
            )
            result = job.result(timeout=GRADIO_IMG2IMG_JOB_TIMEOUT_SECONDS)
            image_result = result[0] if isinstance(result, (list, tuple)) else result

            if isinstance(image_result, dict):
                path = image_result.get("path")
                if path and Path(path).is_file():
                    return Path(path).read_bytes()
                url = image_result.get("url")
                if url:
                    response = requests.get(url, timeout=60)
                    if response.status_code == 200:
                        return response.content

            if isinstance(image_result, str):
                path = Path(image_result)
                if path.is_file():
                    return path.read_bytes()
                if image_result.startswith(("http://", "https://")):
                    response = requests.get(image_result, timeout=60)
                    if response.status_code == 200:
                        return response.content
            return None
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


async def _generate_gradio_img2img(image_bytes: bytes, prompt: str) -> bytes | None:
    return await _run_governed_sync(
        "huggingface.gradio.img2img",
        lambda: _gradio_img2img_sync(image_bytes, prompt),
        timeout_seconds=GRADIO_IMG2IMG_REQUEST_TIMEOUT_SECONDS,
    )


async def _handle_cloudflare_edit(picgeneration_module, message):
    photo = message.photo[-1] if message.photo else (
        message.reply_to_message.photo[-1]
        if message.reply_to_message and message.reply_to_message.photo else None
    )
    if not photo:
        return await message.reply("Хде фото")

    prompt = (message.caption or message.text or "").lower().replace(
        "отредактируй", ""
    ).strip()
    msg = await message.reply("🛠 Редактирую...")
    try:
        img_bytes = await picgeneration_module.download_telegram_image(
            picgeneration_module.bot,
            photo,
        )
        en_prompt = await picgeneration_module.translate_to_en(prompt)
        image = Image.open(BytesIO(img_bytes)).convert("RGB").resize((512, 512))
        buffer = BytesIO()
        image.save(buffer, format="PNG")

        response = await _run_governed_sync(
            "cloudflare.image.edit",
            lambda: requests.post(
                "https://api.cloudflare.com/client/v4/accounts/"
                f"{CLOUDFLARE_ACCOUNT_ID}/ai/run/"
                "@cf/runwayml/stable-diffusion-v1-5-img2img",
                headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"},
                json={
                    "prompt": en_prompt,
                    "image": list(buffer.getvalue()),
                    "strength": 0.6,
                },
                timeout=60,
            ),
            timeout_seconds=CLOUDFLARE_IMAGE_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            await msg.delete()
            return await picgeneration_module.send_generated_photo(
                message,
                response.content,
                "edited.png",
            )
        return await msg.edit_text("Ошибка сервиса.")
    except Exception:
        logging.exception("Cloudflare image edit failed")
        return await msg.edit_text("Не удалось отредактировать.")


def install_into_picgeneration(picgeneration_module) -> None:
    """Wrap expensive image providers used by the legacy image waterfall."""
    if getattr(picgeneration_module, "_media_governor_installed", False):
        return

    picgeneration_module.pollinations_generate = _wrap_async_provider(
        picgeneration_module.pollinations_generate,
        "pollinations.image.generate",
        request_timeout_seconds=POLLINATIONS_IMAGE_TIMEOUT_SECONDS,
        inner_timeout_seconds=110,
    )
    picgeneration_module.hf_generate = _wrap_async_provider(
        picgeneration_module.hf_generate,
        "huggingface.image.generate",
        request_timeout_seconds=HF_IMAGE_TIMEOUT_SECONDS,
        inner_timeout_seconds=70,
    )
    picgeneration_module.cf_generate_t2i = _wrap_async_provider(
        picgeneration_module.cf_generate_t2i,
        "cloudflare.image.generate",
        request_timeout_seconds=CLOUDFLARE_IMAGE_TIMEOUT_SECONDS,
        inner_timeout_seconds=70,
    )
    picgeneration_module.generate_gradio_img2img = _generate_gradio_img2img

    async def governed_edit(message):
        return await _handle_cloudflare_edit(picgeneration_module, message)

    picgeneration_module.handle_edit_command = governed_edit
    picgeneration_module._media_governor_installed = True


def install_into_videogeneration(videogeneration_module) -> None:
    """Wrap long-running video generation without changing command handlers."""
    if getattr(videogeneration_module, "_media_governor_installed", False):
        return

    videogeneration_module.generate_hf_zerogpu_video = _wrap_async_provider(
        videogeneration_module.generate_hf_zerogpu_video,
        "huggingface.zerogpu.video",
        request_timeout_seconds=HF_VIDEO_REQUEST_TIMEOUT_SECONDS,
        inner_timeout_seconds=HF_VIDEO_INNER_TIMEOUT_SECONDS,
    )
    videogeneration_module.generate_video = _wrap_async_provider(
        videogeneration_module.generate_video,
        "pollinations.video.generate",
        request_timeout_seconds=POLLINATIONS_VIDEO_REQUEST_TIMEOUT_SECONDS,
        inner_timeout_seconds=POLLINATIONS_VIDEO_INNER_TIMEOUT_SECONDS,
    )
    videogeneration_module._media_governor_installed = True


__all__ = [
    "install_into_picgeneration",
    "install_into_videogeneration",
]
