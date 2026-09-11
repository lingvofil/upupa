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
YUE2_TIMEOUT_SECONDS = 480
YUE2_MAX_MP3_BYTES = 45 * 1024 * 1024
YUE2_PLANNING_NO_SCORE = "No score"
YUE2_FAST_QUALITY = "Fast · 16 steps"

_generation_lock = asyncio.Lock()


class Yue2GenerationError(RuntimeError):
    pass


class Yue2ConfigurationError(Yue2GenerationError):
    pass


class Yue2QuotaError(Yue2GenerationError):
    def __init__(self, message: str, *, retry_hint: str | None = None):
        super().__init__(message)
        self.retry_hint = retry_hint


def _endpoint_text(endpoint: dict) -> str:
    parts: list[str] = []
    for parameter in endpoint.get("parameters") or []:
        if not isinstance(parameter, dict):
            continue
        for key in ("label", "parameter_name", "component"):
            value = parameter.get(key)
            if value:
                parts.append(str(value))
    return " ".join(parts).lower()


def _looks_like_song_endpoint(api_name: str, endpoint: dict) -> bool:
    name = str(api_name).lower()
    text = _endpoint_text(endpoint)
    if "style" not in text or "lyric" not in text:
        return False
    return "generate" in name or "song" in name or "audio" in name


def _discover_generate_endpoint(client: Client) -> tuple[str | None, int | None, dict | None]:
    try:
        info = client.view_api(print_info=False, return_format="dict") or {}
        named = info.get("named_endpoints") or {}
        for api_name, endpoint in named.items():
            if isinstance(endpoint, dict) and _looks_like_song_endpoint(str(api_name), endpoint):
                return str(api_name), None, endpoint

        for api_name, endpoint in named.items():
            if isinstance(endpoint, dict):
                text = _endpoint_text(endpoint)
                if "style" in text and "lyric" in text:
                    return str(api_name), None, endpoint

        unnamed = info.get("unnamed_endpoints") or {}
        for fn_index, endpoint in unnamed.items():
            if isinstance(endpoint, dict) and "style" in _endpoint_text(endpoint) and "lyric" in _endpoint_text(endpoint):
                return None, int(fn_index), endpoint
    except Exception as exc:
        logging.warning("[song][yue2] API discovery failed: %s", exc)

    return "/generate_song", None, None


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_strings(child)


def _matching_choice(parameter: dict, marker: str, fallback: str) -> str:
    marker = marker.lower()
    for value in _walk_strings(parameter):
        if marker in value.lower() and len(value) <= 120:
            return value
    return fallback


def _parameter_key(parameter: dict) -> str:
    return " ".join(
        str(parameter.get(key) or "")
        for key in ("parameter_name", "label", "component")
    ).lower()


def _parameter_default(parameter: dict) -> tuple[bool, Any]:
    for key in ("parameter_default", "default"):
        if key in parameter:
            return True, parameter[key]
    return False, None


def _build_endpoint_args(
    endpoint: dict | None,
    *,
    style_prompt: str,
    lyrics: str,
    seed: int,
) -> tuple[Any, ...]:
    if not endpoint:
        return (
            style_prompt,
            lyrics,
            YUE2_PLANNING_NO_SCORE,
            YUE2_FAST_QUALITY,
            seed,
        )

    args: list[Any] = []
    for parameter in endpoint.get("parameters") or []:
        if not isinstance(parameter, dict):
            continue
        key = _parameter_key(parameter)
        if "style" in key:
            args.append(style_prompt)
        elif "lyric" in key:
            args.append(lyrics)
        elif "symbolic" in key or "planning" in key or "score" in key or "cot" in key:
            args.append(_matching_choice(parameter, "no score", YUE2_PLANNING_NO_SCORE))
        elif "quality" in key or "render" in key or "step" in key:
            args.append(_matching_choice(parameter, "fast", YUE2_FAST_QUALITY))
        elif "seed" in key:
            args.append(seed)
        else:
            has_default, default = _parameter_default(parameter)
            if not has_default:
                raise Yue2GenerationError(f"Unknown required YuE2 Space parameter: {key or parameter!r}")
            args.append(default)
    return tuple(args)


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
        # Prefer explicitly audio/mp3-shaped fields before generic FileData fields.
        for key in ("mp3", "audio", "value", "path", "url"):
            if key in value:
                result.extend(_candidate_strings(value[key]))
        for key, item in value.items():
            if key not in {"mp3", "audio", "value", "path", "url"}:
                result.extend(_candidate_strings(item))
    return result


def _is_mp3_reference(value: str) -> bool:
    clean = value.lower().split("?", 1)[0].split("#", 1)[0]
    return clean.endswith(".mp3")


def _download_or_copy_mp3(result: Any) -> Path:
    candidates = _candidate_strings(result)
    mp3_candidates = [value for value in candidates if _is_mp3_reference(value)]
    if not mp3_candidates:
        raise Yue2GenerationError("YuE2 Space did not return an MP3 result")

    fd, output_name = tempfile.mkstemp(prefix="upupa_yue2_", suffix=".mp3")
    Path(output_name).unlink(missing_ok=True)
    try:
        for value in mp3_candidates:
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
            api_name, fn_index, endpoint = _discover_generate_endpoint(client)
            args = _build_endpoint_args(
                endpoint,
                style_prompt=style_prompt,
                lyrics=lyrics,
                seed=seed,
            )
            submit_kwargs = {"api_name": api_name} if api_name else {"fn_index": fn_index}
            logging.info(
                "[song][yue2] submit space=%s endpoint=%s fn_index=%s seed=%s",
                YUE2_SPACE_ID,
                api_name,
                fn_index,
                seed,
            )
            job = client.submit(*args, **submit_kwargs)
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
