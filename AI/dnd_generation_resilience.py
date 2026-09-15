"""Fail-fast text generation for DnD without long Gemini retry storms."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from google import genai
from google.genai import types as genai_types

from core.settings import (
    GEMINI_KEYS_POOL,
    GROQ_API_KEY,
    MODEL_QUEUE_DEFAULT,
    MODEL_QUEUE_SPECIAL,
    SPECIAL_CHAT_ID,
)
from infrastructure.ai.clients import groq_ai
from infrastructure.ai.execution import ai_execution_lane, run_ai_provider_call


DND_GEMINI_HTTP_TIMEOUT_MS = 12_000
DND_GEMINI_GOVERNOR_TIMEOUT_SECONDS = 15.0
DND_GEMINI_QUEUE_TIMEOUT_SECONDS = 5.0
DND_GEMINI_ATTEMPTS = 2
DND_GEMINI_CIRCUIT_SECONDS = 45.0
DND_GROQ_FALLBACK_TIMEOUT_SECONDS = 18.0
DND_AUX_HTTP_TIMEOUT_MS = 6_000
DND_AUX_GOVERNOR_TIMEOUT_SECONDS = 8.0
DND_AUX_QUEUE_TIMEOUT_SECONDS = 2.0
# Groq's on-demand tier for the current fallback model is capped at 8k TPM.
# Cyrillic DnD history can tokenize much denser than Latin text, so keep the
# normal fallback comfortably below that ceiling and retry once even smaller.
DND_FALLBACK_PROMPT_MAX_CHARS = 12_000
DND_FALLBACK_RETRY_PROMPT_MAX_CHARS = 7_000
DND_GROQ_FALLBACK_MAX_TOKENS = 900
DND_GROQ_FALLBACK_RETRY_MAX_TOKENS = 700


_state_lock = threading.Lock()
_key_cursor = 0
_gemini_circuit_until = 0.0
_client_cache: dict[tuple[str, int], genai.Client] = {}


class DndGeminiCircuitOpen(RuntimeError):
    """Gemini was recently degraded, so DnD skips it briefly."""


def _get_client(api_key: str, timeout_ms: int) -> genai.Client:
    key = (api_key, int(timeout_ms))
    with _state_lock:
        client = _client_cache.get(key)
        if client is None:
            client = genai.Client(
                api_key=api_key,
                http_options=genai_types.HttpOptions(timeout=int(timeout_ms)),
            )
            _client_cache[key] = client
        return client


def _model_queue(chat_id: int | None) -> list[str]:
    if chat_id is not None and str(chat_id) == str(SPECIAL_CHAT_ID):
        queue = MODEL_QUEUE_SPECIAL
    else:
        queue = MODEL_QUEUE_DEFAULT
    return [str(name).removeprefix("models/") for name in queue if name]


def _attempt_pairs(chat_id: int | None, attempts: int) -> list[tuple[str, str]]:
    keys = list(GEMINI_KEYS_POOL)
    models = _model_queue(chat_id)
    if not keys or not models or attempts <= 0:
        return []

    global _key_cursor
    with _state_lock:
        start = _key_cursor % len(keys)
        _key_cursor = (start + max(1, attempts)) % len(keys)

    model_span = min(2, len(models))
    return [
        (keys[(start + index) % len(keys)], models[index % model_span])
        for index in range(attempts)
    ]


def _circuit_is_open() -> bool:
    with _state_lock:
        return time.monotonic() < _gemini_circuit_until


def _open_circuit() -> None:
    global _gemini_circuit_until
    with _state_lock:
        _gemini_circuit_until = max(
            _gemini_circuit_until,
            time.monotonic() + DND_GEMINI_CIRCUIT_SECONDS,
        )


def _close_circuit() -> None:
    global _gemini_circuit_until
    with _state_lock:
        _gemini_circuit_until = 0.0


def _extract_text(response: Any) -> str:
    try:
        text = getattr(response, "text", None)
    except Exception:
        text = None
    if text:
        return str(text).strip()
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                return str(part_text).strip()
    return ""


def _status_code(error: Exception) -> int | None:
    value = getattr(error, "code", None) or getattr(error, "status_code", None)
    if value is None and getattr(error, "response", None) is not None:
        value = getattr(error.response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_transient(error: Exception) -> bool:
    code = _status_code(error)
    text = str(error).casefold()
    return (
        code in {429, 500, 502, 503, 504}
        or "timeout" in text
        or "timed out" in text
        or "temporar" in text
        or "resourceexhausted" in text
        or "quotaexceeded" in text
        or "429" in text
        or "503" in text
    )


def _is_request_too_large(error: Exception) -> bool:
    """Return whether Groq rejected the fallback because the prompt is too large."""
    code = _status_code(error)
    text = str(error).casefold()
    return (
        code == 413
        or "request too large" in text
        or "tokens per minute" in text
        or "requested" in text and "tpm" in text
    )


def _history_contents(session, prompt: str):
    contents = []
    for item in getattr(session, "conversation", None) or []:
        if not isinstance(item, dict) or item.get("content") is None:
            continue
        source_role = item.get("role")
        role = "model" if source_role in {"assistant", "model"} else "user"
        contents.append({"role": role, "parts": [{"text": str(item["content"])}]})
    contents.append({"role": "user", "parts": [{"text": str(prompt)}]})
    return contents


def _run_gemini_sync(
    session,
    prompt: str,
    *,
    attempts: int,
    http_timeout_ms: int,
    governor_timeout_seconds: float,
    queue_timeout_seconds: float,
    include_history: bool,
    lane: str,
    update_circuit: bool,
) -> str:
    if update_circuit and _circuit_is_open():
        raise DndGeminiCircuitOpen("DnD Gemini circuit is temporarily open")

    pairs = _attempt_pairs(getattr(session, "chat_id", None), attempts)
    if not pairs:
        raise RuntimeError("DnD Gemini keys/models are not configured")

    contents = _history_contents(session, prompt) if include_history else str(prompt)
    errors: list[Exception] = []
    saw_transient = False
    config = genai_types.GenerateContentConfig(
        temperature=0.8,
        max_output_tokens=900,
    )

    for api_key, model_name in pairs:
        try:
            client = _get_client(api_key, http_timeout_ms)
            with ai_execution_lane(lane):
                response = run_ai_provider_call(
                    "dnd.gemini.generate_content",
                    client.models.generate_content,
                    model=model_name,
                    contents=contents,
                    config=config,
                    timeout_seconds=governor_timeout_seconds,
                    queue_timeout_seconds=queue_timeout_seconds,
                )
            text = _extract_text(response)
            if not text:
                raise RuntimeError("DnD Gemini returned empty text")
            if update_circuit:
                _close_circuit()
            logging.info(
                "DnD Gemini success chat_id=%s model=%s",
                getattr(session, "chat_id", None),
                model_name,
            )
            return text
        except Exception as exc:
            errors.append(exc)
            transient = _is_transient(exc)
            saw_transient = saw_transient or transient
            logging.warning(
                "DnD Gemini fast attempt failed chat_id=%s model=%s transient=%s error=%s",
                getattr(session, "chat_id", None),
                model_name,
                transient,
                exc,
            )

    if update_circuit and saw_transient:
        _open_circuit()
        logging.warning(
            "DnD Gemini circuit opened chat_id=%s seconds=%s",
            getattr(session, "chat_id", None),
            int(DND_GEMINI_CIRCUIT_SECONDS),
        )
    raise RuntimeError(f"DnD Gemini fast path failed: {errors[-1] if errors else 'unknown error'}")


def _fallback_prompt(session, prompt: str, *, max_chars: int = DND_FALLBACK_PROMPT_MAX_CHARS) -> str:
    rows = []
    for item in getattr(session, "conversation", None) or []:
        if not isinstance(item, dict) or item.get("content") is None:
            continue
        role = "assistant" if item.get("role") in {"assistant", "model"} else "user"
        rows.append(f"{role}: {item['content']}")
    rows.append(f"user: {prompt}")
    full = "\n\n".join(rows)
    if len(full) <= max_chars:
        return full

    marker = "\n\n[...середина истории сокращена...]\n\n"
    first = rows[0] if rows else ""
    head_budget = min(len(first), min(4_000, max_chars // 3))
    head = first[:head_budget]
    tail_source = "\n\n".join(rows[1:]) if len(rows) > 1 else first
    tail_budget = max(0, max_chars - len(head) - len(marker))
    if tail_budget:
        compact = f"{head}{marker}{tail_source[-tail_budget:]}"
    else:
        compact = head[:max_chars]
    return compact[:max_chars]


def _run_groq_sync(
    session,
    prompt: str,
    *,
    max_prompt_chars: int = DND_FALLBACK_PROMPT_MAX_CHARS,
    max_tokens: int = DND_GROQ_FALLBACK_MAX_TOKENS,
) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("Groq is not configured")
    fallback_prompt = _fallback_prompt(session, prompt, max_chars=max_prompt_chars)
    with ai_execution_lane("interactive"):
        text = groq_ai.generate_text(
            fallback_prompt,
            max_tokens=max_tokens,
            temperature=0.8,
        )
    text = str(text or "").strip()
    if not text or text == "Ключ Groq не настроен":
        raise RuntimeError("Groq returned empty text")
    return text


async def _run_groq_fallback(session, prompt: str) -> str:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _run_groq_sync,
                session,
                prompt,
                max_prompt_chars=DND_FALLBACK_PROMPT_MAX_CHARS,
                max_tokens=DND_GROQ_FALLBACK_MAX_TOKENS,
            ),
            timeout=DND_GROQ_FALLBACK_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        if not _is_request_too_large(exc):
            raise
        logging.warning(
            "DnD Groq fallback request too large chat_id=%s; retrying compact prompt chars=%s",
            getattr(session, "chat_id", None),
            DND_FALLBACK_RETRY_PROMPT_MAX_CHARS,
        )
        return await asyncio.wait_for(
            asyncio.to_thread(
                _run_groq_sync,
                session,
                prompt,
                max_prompt_chars=DND_FALLBACK_RETRY_PROMPT_MAX_CHARS,
                max_tokens=DND_GROQ_FALLBACK_RETRY_MAX_TOKENS,
            ),
            timeout=DND_GROQ_FALLBACK_TIMEOUT_SECONDS,
        )


async def _generate_main_text(session, prompt: str) -> str:
    gemini_error: Exception | None = None
    try:
        return await asyncio.to_thread(
            _run_gemini_sync,
            session,
            prompt,
            attempts=DND_GEMINI_ATTEMPTS,
            http_timeout_ms=DND_GEMINI_HTTP_TIMEOUT_MS,
            governor_timeout_seconds=DND_GEMINI_GOVERNOR_TIMEOUT_SECONDS,
            queue_timeout_seconds=DND_GEMINI_QUEUE_TIMEOUT_SECONDS,
            include_history=True,
            lane="interactive",
            update_circuit=True,
        )
    except Exception as exc:
        gemini_error = exc
        logging.warning(
            "DnD Gemini degraded chat_id=%s; switching to Groq: %s",
            getattr(session, "chat_id", None),
            exc,
        )

    try:
        text = await _run_groq_fallback(session, prompt)
        logging.info(
            "DnD provider fallback success chat_id=%s provider=groq",
            getattr(session, "chat_id", None),
        )
        return text
    except Exception as exc:
        raise RuntimeError(
            f"DnD generation failed after Gemini/Groq fallback: gemini={gemini_error}; groq={exc}"
        ) from exc


async def generate_auxiliary_text(session, prompt: str) -> str | None:
    """One short best-effort Gemini call for non-critical DnD post-processing.

    It never mutates the canonical DnD conversation and never falls through the
    long provider chain. During an open Gemini circuit it is skipped entirely.
    """
    if _circuit_is_open():
        return None
    try:
        return await asyncio.to_thread(
            _run_gemini_sync,
            session,
            prompt,
            attempts=1,
            http_timeout_ms=DND_AUX_HTTP_TIMEOUT_MS,
            governor_timeout_seconds=DND_AUX_GOVERNOR_TIMEOUT_SECONDS,
            queue_timeout_seconds=DND_AUX_QUEUE_TIMEOUT_SECONDS,
            include_history=False,
            lane="background",
            update_circuit=False,
        )
    except Exception as exc:
        logging.info(
            "DnD auxiliary generation skipped chat_id=%s error=%s",
            getattr(session, "chat_id", None),
            exc,
        )
        return None


def configure_dnd_generation_resilience(dnd) -> None:
    """Install a bounded Gemini path plus one cross-provider fallback for DnD."""
    if getattr(dnd, "_upupa_dnd_generation_resilience_configured", False):
        return

    original_generate = dnd.generate_session_response

    async def resilient_generate(session, prompt: str) -> str:
        if getattr(session, "active_model", None) != "gemini" or not hasattr(session, "conversation"):
            return await original_generate(session, prompt)

        result = await _generate_main_text(session, prompt)
        session.conversation.append({"role": "user", "content": str(prompt)})
        session.conversation.append({"role": "assistant", "content": result})
        if getattr(dnd, "dnd_sessions", {}).get(getattr(session, "chat_id", None)) is session:
            dnd.persist_dnd_sessions()
        return result

    dnd.generate_session_response = resilient_generate
    dnd._upupa_dnd_generation_resilience_base = resilient_generate
    dnd._upupa_dnd_generation_resilience_configured = True


__all__ = [
    "DndGeminiCircuitOpen",
    "configure_dnd_generation_resilience",
    "generate_auxiliary_text",
]
