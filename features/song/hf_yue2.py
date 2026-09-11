"""Remote YuE2 generation through the private Hugging Face Space."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import random
import re
import shutil
import tempfile
from typing import Any

import requests
from gradio_client import Client

from core.settings import HUGGINGFACE_TOKEN


YUE2_SPACE_ID = "lingvofil/upupa-yue2"
YUE2_API_NAME = "/generate_song"
YUE2_TIMEOUT_SECONDS = 480
YUE2_MAX_MP3_BYTES = 45 * 1024 * 1024
YUE2_PLANNING_MODE = "off"
YUE2_RENDER_QUALITY = 16

_generation_lock = asyncio.Lock()


class Yue2GenerationError(RuntimeError):
    pass


class Yue2ConfigurationError(Yue2GenerationError):
    pass


class Yue2QuotaError(Yue2GenerationError):
    def __init__(self, message: str, *, retry_hint: str | None = None):
        super().__init__(message)
        self.retry_hint = retry_hint


def _candidate_strings(value: Any) -> list[str]:
    result: list[str] = []
    if value is None:
        return result
    if isinstance(value, Path):
        return [str(value)]
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        for item in value:
            result.extend(_candidate_strings(item))
        return result
    if isinstance(value, dict):
        for key in ("path", "url", "value", "name"):
            if key in value:
                result.extend(_candidate_strings(value[key]))
        for key, item in value.items():
            if key not in {"path", "url", "value", "name"}:
                result.extend(_candidate_strings(item))
    return result


def _is_mp3_reference(value: str) -> bool:
    clean = value.lower().split("?", 1)[0].split("#", 1)[0]
    return clean.endswith(".mp3")


def _download_or_copy_mp3(result: Any) -> Path:
    """Copy the first /generate_song result (documented MP3 output) to our own temp file."""
    if not isinstance(result, (list, tuple)) or not result:
        raise Yue2GenerationError("YuE2 Space returned an unexpected result")

    # /generate_song contract:
    #   result[0] -> MP3, result[1] -> FLAC, result[2] -> editable ABC score.
    mp3_result = result[0]
    candidates = [value for value in _candidate_strings(mp3_result) if _is_mp3_reference(value)]
    if not candidates:
        raise Yue2GenerationError("YuE2 Space did not return the documented MP3 output")

    fd, output_name = tempfile.mkstemp(prefix="upupa_yue2_", suffix=".mp3")
    Path(output_name).unlink(missing_ok=True)
    try:
        for value in candidates:
            path = Path(value)
            if path.is_file():
                if not 0 < path.stat().st_size <= YUE2_MAX_MP3_BYTES:
                    continue
                shutil.copyfile(path, output_name)
                return Path(output_name)

            if value.startswith(("https://", "http://")):
                response = requests.get(value, timeout=90)
                response.raise_for_status()
                data = response.content
                if not 0 < len(data) <= YUE2_MAX_MP3_BYTES:
                    continue
                Path(output_name).write_bytes(data)
                return Path(output_name)
    except Exception:
        Path(output_name).unlink(missing_ok=True)
        raise
    finally:
        try:
            import os
            os.close(fd)
        except OSError:
            pass

    Path(output_name).unlink(missing_ok=True)
    raise Yue2GenerationError("YuE2 returned no readable MP3 file")


def _extract_retry_hint(text: str) -> str | None:
    compact = re.sub(r"\s+", " ", text or "").strip()
    patterns = (
        r"(?:try again|retry)(?: in| after)?\s+([^.;]+)",
        r"(?:resets?|reset)(?: in| after| at)?\s+([^.;]+)",
        r"available again(?: in| after| at)?\s+([^.;]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if match:
            hint = match.group(1).strip()
            return hint[:100] or None
    return None


def _classify_exception(exc: Exception) -> Exception:
    text = str(exc)
    low = text.lower()
    quota_markers = (
        "quota",
        "rate limit",
        "rate_limit",
        "too many requests",
        "429",
        "zero gpu",
        "zerogpu",
        "gpu limit",
        "exceeded your gpu",
    )
    if any(marker in low for marker in quota_markers):
        return Yue2QuotaError(text, retry_hint=_extract_retry_hint(text))
    if any(marker in low for marker in ("401", "403", "unauthorized", "forbidden", "private space")):
        return Yue2ConfigurationError("Hugging Face rejected access to the private YuE2 Space")
    return Yue2GenerationError(text or exc.__class__.__name__)


def _generate_sync(lyrics: str, style_prompt: str) -> Path:
    token = (HUGGINGFACE_TOKEN or "").strip()
    if not token:
        raise Yue2ConfigurationError("HF_TOKEN/HUGGINGFACE_TOKEN is not configured")

    seed = random.SystemRandom().randint(1, 2_147_483_647)
    with tempfile.TemporaryDirectory(prefix="upupa_yue2_download_") as download_dir:
        client = None
        try:
            client = Client(
                YUE2_SPACE_ID,
                token=token,
                verbose=False,
                download_files=download_dir,
            )
            logging.info(
                "[song][yue2] submit space=%s endpoint=%s planning=%s quality=%s seed=%s",
                YUE2_SPACE_ID,
                YUE2_API_NAME,
                YUE2_PLANNING_MODE,
                YUE2_RENDER_QUALITY,
                seed,
            )
            job = client.submit(
                style=style_prompt,
                lyrics=lyrics,
                planning_mode=YUE2_PLANNING_MODE,
                render_quality=YUE2_RENDER_QUALITY,
                seed=seed,
                api_name=YUE2_API_NAME,
            )
            result = job.result(timeout=YUE2_TIMEOUT_SECONDS)
            output = _download_or_copy_mp3(result)
            logging.info("[song][yue2] generated mp3 bytes=%s", output.stat().st_size)
            return output
        except Yue2GenerationError:
            raise
        except Exception as exc:
            classified = _classify_exception(exc)
            logging.warning("[song][yue2] generation failed: %s", classified)
            raise classified from exc
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


async def generate_yue2_song(lyrics: str, style_prompt: str) -> Path:
    """Render one song remotely without blocking aiogram; Space work is serialized locally."""
    async with _generation_lock:
        return await asyncio.to_thread(_generate_sync, lyrics, style_prompt)
