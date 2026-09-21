"""AI Horde text-to-image fallback provider.

The public Horde is intentionally used as a best-effort reserve: GigaChat stays
primary, while this provider gives Upupa an independent free image backend
before Pollinations/Hugging Face/Cloudflare.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Callable, Optional

import requests

from core.settings import AIHORDE_API_KEY
from infrastructure.ai.execution import run_ai_provider_call


AIHORDE_API_URL = "https://aihorde.net/api/v2"
AIHORDE_MODEL = "Deliberate 3.0"
AIHORDE_CLIENT_AGENT = "upupa:1.0:https://github.com/lingvofil/upupa"
AIHORDE_POLL_INTERVAL_SECONDS = 5
AIHORDE_MAX_WAIT_SECONDS = 120
AIHORDE_SUBMIT_TIMEOUT_SECONDS = 30
AIHORDE_STATUS_TIMEOUT_SECONDS = 20
AIHORDE_DOWNLOAD_TIMEOUT_SECONDS = 60
AIHORDE_REQUEST_TIMEOUT_SECONDS = 180
AIHORDE_NEGATIVE_PROMPT = "blurry, deformed, bad anatomy, low quality"


def _request_headers() -> dict[str, str]:
    return {
        "apikey": AIHORDE_API_KEY or "0000000000",
        "Client-Agent": AIHORDE_CLIENT_AGENT,
    }


def _decode_generation_image(value: str) -> Optional[bytes]:
    if not value:
        return None

    if value.startswith(("http://", "https://")):
        response = requests.get(value, timeout=AIHORDE_DOWNLOAD_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.content if len(response.content) > 1000 else None

    try:
        payload = value.split(",", 1)[-1]
        image = base64.b64decode(payload)
    except Exception as exc:
        logging.warning("AI Horde image decode failed: %s", exc)
        return None
    return image if len(image) > 1000 else None


def _generate_aihorde_image_sync(
    prompt: str,
    should_continue: Callable[[], bool] | None = None,
) -> Optional[bytes]:
    """Submit one Horde job, wait for it, and return decoded image bytes."""
    prompt = (prompt or "").strip()
    if not prompt:
        return None
    if should_continue is not None and not should_continue():
        logging.info("AI Horde image skipped before submit: request is stale")
        return None

    headers = _request_headers()
    payload = {
        "prompt": f"{prompt[:1800]} ### {AIHORDE_NEGATIVE_PROMPT}",
        "params": {
            "n": 1,
            "width": 512,
            "height": 512,
            "steps": 25,
            "cfg_scale": 7,
            "sampler_name": "k_euler_a",
            "karras": True,
        },
        "models": [AIHORDE_MODEL],
        "nsfw": False,
        "censor_nsfw": True,
        "r2": True,
        "shared": False,
        "slow_workers": True,
    }

    try:
        response = requests.post(
            f"{AIHORDE_API_URL}/generate/async",
            headers=headers,
            json=payload,
            timeout=AIHORDE_SUBMIT_TIMEOUT_SECONDS,
        )
        if response.status_code != 202:
            logging.warning(
                "AI Horde submit HTTP %s: %s",
                response.status_code,
                response.text[:300],
            )
            return None
        job_id = response.json().get("id")
        if not job_id:
            logging.warning("AI Horde submit returned no job id")
            return None

        logging.info("AI Horde job submitted: id=%s model=%s", job_id, AIHORDE_MODEL)
        started = time.monotonic()
        while time.monotonic() - started < AIHORDE_MAX_WAIT_SECONDS:
            if should_continue is not None and not should_continue():
                logging.info("AI Horde wait stopped because image request became stale: id=%s", job_id)
                return None
            status_response = requests.get(
                f"{AIHORDE_API_URL}/generate/check/{job_id}",
                headers=headers,
                timeout=AIHORDE_STATUS_TIMEOUT_SECONDS,
            )
            status_response.raise_for_status()
            status = status_response.json()
            if status.get("faulted"):
                logging.warning("AI Horde job faulted: id=%s status=%s", job_id, status)
                return None
            if status.get("done"):
                break
            time.sleep(AIHORDE_POLL_INTERVAL_SECONDS)
        else:
            logging.warning("AI Horde job timeout after %ss: id=%s", AIHORDE_MAX_WAIT_SECONDS, job_id)
            return None

        result_response = requests.get(
            f"{AIHORDE_API_URL}/generate/status/{job_id}",
            headers=headers,
            timeout=AIHORDE_STATUS_TIMEOUT_SECONDS,
        )
        result_response.raise_for_status()
        generations = result_response.json().get("generations") or []
        if not generations:
            logging.warning("AI Horde job completed without generations: id=%s", job_id)
            return None

        generation = generations[0]
        image = _decode_generation_image(generation.get("img") or "")
        if image:
            logging.info(
                "AI Horde image ready: model=%s worker=%s size=%s bytes",
                generation.get("model") or AIHORDE_MODEL,
                generation.get("worker_name"),
                len(image),
            )
        return image
    except Exception as exc:
        logging.warning("AI Horde image generation failed: %s", exc)
        return None


def _generate_aihorde_image_governed(
    prompt: str,
    should_continue: Callable[[], bool] | None = None,
) -> Optional[bytes]:
    return run_ai_provider_call(
        "aihorde.image.generate",
        _generate_aihorde_image_sync,
        prompt,
        should_continue,
        timeout_seconds=AIHORDE_REQUEST_TIMEOUT_SECONDS,
    )


async def generate_aihorde_image(
    prompt: str,
    *,
    should_continue: Callable[[], bool] | None = None,
) -> Optional[bytes]:
    """Run the blocking Horde workflow under the process-wide AI governor."""
    try:
        return await asyncio.to_thread(
            _generate_aihorde_image_governed,
            prompt,
            should_continue,
        )
    except Exception as exc:
        logging.warning("AI Horde governed request failed: %s", exc)
        return None
