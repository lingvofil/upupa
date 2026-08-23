"""Gemini SDK adapters and fallback routing.

The public classes keep the legacy Upupa interface while the provider-specific
implementation lives below the application/AI layer.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, List, Optional, Tuple

from google import genai
from google.genai import types as genai_types


PER_KEY_MIN_DELAY = 2.5

_last_call_ts: dict[str, float] = {}
_throttle_lock = threading.Lock()
_genai_lock = threading.RLock()
_client_cache: dict[str, genai.Client] = {}


def _throttle_key(api_key: str) -> None:
    """Keep a minimum delay between requests made with the same API key."""
    while True:
        with _throttle_lock:
            now = time.time()
            last = _last_call_ts.get(api_key, 0.0)
            wait = PER_KEY_MIN_DELAY - (now - last)
            if wait <= 0:
                _last_call_ts[api_key] = now
                return
        time.sleep(wait)


def _extract_error_details(error: Exception) -> Tuple[Optional[int], str]:
    status_code = getattr(error, "code", None) or getattr(error, "status_code", None)
    if (
        status_code is None
        and hasattr(error, "response")
        and getattr(error, "response", None) is not None
    ):
        status_code = getattr(error.response, "status_code", None)
    return status_code, error.__class__.__name__


def _is_retryable(error: Exception) -> bool:
    status_code, error_type = _extract_error_details(error)
    text = str(error).lower()
    if error_type == "EmptyModelResponseError":
        return False
    if status_code in (429, 503):
        return True
    if error_type in ("ResourceExhausted", "QuotaExceeded"):
        return True
    return any(
        marker in text
        for marker in ("429", "503", "resourceexhausted", "quotaexceeded")
    )


class EmptyModelResponseError(RuntimeError):
    """Gemini returned a successful response without text content."""


def _extract_response_text(response: Any) -> str:
    try:
        text = getattr(response, "text", None)
    except Exception:
        text = None
    if text:
        return str(text)

    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                return str(part_text)
    return ""


def _empty_response_details(response: Any) -> str:
    details = []
    for candidate in getattr(response, "candidates", None) or []:
        finish_reason = getattr(candidate, "finish_reason", None)
        if finish_reason:
            details.append(f"finish_reason={finish_reason}")
        safety = getattr(candidate, "safety_ratings", None)
        if safety:
            details.append(f"safety_ratings={safety}")
    return "; ".join(details) or "no candidate text"


def _get_client(api_key: str) -> genai.Client:
    with _genai_lock:
        client = _client_cache.get(api_key)
        if client is None:
            client = genai.Client(api_key=api_key)
            _client_cache[api_key] = client
        return client


def _normalize_part(item):
    """Convert the old {mime_type, data} blob into a google-genai Part."""
    if isinstance(item, dict) and set(item) == {"mime_type", "data"}:
        return genai_types.Part.from_bytes(
            data=item["data"],
            mime_type=item["mime_type"],
        )
    return item


def _normalize_contents(contents):
    if isinstance(contents, (list, tuple)):
        return [_normalize_part(item) for item in contents]
    return contents


def _normalize_history(history):
    if not history:
        return None
    result = []
    for item in history:
        if isinstance(item, dict):
            parts = [
                {"text": part} if isinstance(part, str) else part
                for part in item.get("parts", [])
            ]
            result.append({"role": item.get("role", "user"), "parts": parts})
        else:
            result.append(item)
    return result


def _build_config(kwargs: dict):
    """Build GenerateContentConfig from legacy google-generativeai kwargs."""
    cfg = {}
    kwargs.pop("require_text", None)
    generation_config = kwargs.pop("generation_config", None)
    if generation_config:
        cfg.update(dict(generation_config))
    safety = kwargs.pop("safety_settings", None)
    if safety:
        if isinstance(safety, dict):
            safety = [
                {"category": category, "threshold": threshold}
                for category, threshold in safety.items()
            ]
        cfg["safety_settings"] = safety
    cfg.update(kwargs)
    return genai_types.GenerateContentConfig(**cfg) if cfg else None


class _ChatAdapter:
    def __init__(self, chat):
        self._chat = chat

    def send_message(self, content, **kwargs):
        config = _build_config(kwargs)
        content = _normalize_contents(content)
        if config is not None:
            return self._chat.send_message(content, config=config)
        return self._chat.send_message(content)

    @property
    def history(self):
        return self._chat.get_history()


class GeminiModel:
    """Legacy GenerativeModel-shaped adapter over google-genai Client."""

    def __init__(self, client: genai.Client, model_name: str):
        self._client = client
        self.model_name = model_name

    def generate_content(self, contents, **kwargs):
        return self._client.models.generate_content(
            model=self.model_name,
            contents=_normalize_contents(contents),
            config=_build_config(kwargs),
        )

    def start_chat(self, history=None):
        return _ChatAdapter(
            self._client.chats.create(
                model=self.model_name,
                history=_normalize_history(history),
            )
        )


class FallbackChatSession:
    def __init__(
        self,
        wrapper,
        history: Optional[List[Any]] = None,
        model_queue: Optional[List[str]] = None,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ):
        self.wrapper = wrapper
        self.history = history or []
        self.model_queue = model_queue or wrapper.default_queue
        self.chat_id = chat_id
        self.user_id = user_id

    def send_message(self, content, chat_id=None, **kwargs):
        effective_chat_id = chat_id if chat_id is not None else self.chat_id
        return self.wrapper._run_with_fallback(
            action_name="start_chat.send_message",
            chat_id=effective_chat_id,
            request_fn=lambda model_obj: self._send_with_model(
                model_obj,
                content,
                **kwargs,
            ),
        )

    def _send_with_model(self, model_obj, content, **kwargs):
        chat = model_obj.start_chat(history=self.history)
        response = chat.send_message(content, **kwargs)
        self.history = chat.history
        return response


class ModelFallbackWrapper:
    GEMINI_LIMIT_EXHAUSTED_MESSAGE = (
        "⚠️ Все лимиты Gemini временно исчерпаны. Попробуй позже."
    )

    def __init__(
        self,
        default_queue: List[str],
        special_queue: List[str],
        keys_pool: Optional[List[str]] = None,
    ):
        self.default_queue = default_queue
        self.special_queue = special_queue
        self.keys_pool = [key for key in (keys_pool or []) if key]
        self._key_rr_cursor = 0
        self._max_retries_per_pair = 3
        self.last_used_model_name: Optional[str] = None

    def _get_queue(self, chat_id: Optional[int]):
        from core.settings import SPECIAL_CHAT_ID

        if chat_id and str(chat_id) == str(SPECIAL_CHAT_ID):
            return self.special_queue
        return self.default_queue

    def generate_content(
        self,
        prompt,
        *,
        chat_id=None,
        require_text: bool = False,
        **kwargs,
    ):
        return self._run_with_fallback(
            action_name="generate_content",
            chat_id=chat_id,
            request_fn=lambda model_obj: model_obj.generate_content(prompt, **kwargs),
            require_text=require_text,
        )

    def generate_custom(self, model_name: str, *args, **kwargs):
        model_name = self._normalize_model_name(model_name)
        temp_wrapper = ModelFallbackWrapper(
            [model_name],
            [model_name],
            keys_pool=self.keys_pool,
        )
        require_text = kwargs.pop("require_text", False)
        return temp_wrapper._run_with_fallback(
            action_name="generate_custom",
            chat_id=kwargs.pop("chat_id", None),
            request_fn=lambda model_obj: model_obj.generate_content(*args, **kwargs),
            require_text=require_text,
        )

    def start_chat(self, history=None, chat_id=None, user_id=None):
        return FallbackChatSession(
            self,
            history=history,
            model_queue=self._get_queue(chat_id),
            chat_id=chat_id,
            user_id=user_id,
        )

    @property
    def model_names(self):
        return self.default_queue

    def _normalize_model_name(self, model_name: str) -> str:
        return model_name.removeprefix("models/")

    def _iter_key_indices(self):
        if not self.keys_pool:
            return []
        count = len(self.keys_pool)
        start = self._key_rr_cursor % count
        order = [(start + index) % count for index in range(count)]
        self._key_rr_cursor = (start + 1) % count
        return order

    def _build_model(self, api_key: str, model_name: str):
        return GeminiModel(_get_client(api_key), model_name)

    def _run_with_fallback(
        self,
        action_name: str,
        chat_id: Optional[int],
        request_fn: Callable,
        require_text: bool = False,
    ):
        model_queue = [
            self._normalize_model_name(name)
            for name in self._get_queue(chat_id)
        ]
        key_indices = self._iter_key_indices()
        if not key_indices:
            raise RuntimeError("Gemini API keys pool is empty")

        hard_failures: List[Exception] = []
        temporary_failure_only = True

        for model_name in model_queue:
            for key_idx in key_indices:
                api_key = self.keys_pool[key_idx]
                for attempt in range(1, self._max_retries_per_pair + 1):
                    try:
                        _throttle_key(api_key)
                        model_obj = self._build_model(api_key, model_name)
                        result = request_fn(model_obj)
                        if require_text and not _extract_response_text(result).strip():
                            raise EmptyModelResponseError(
                                _empty_response_details(result)
                            )
                        self.last_used_model_name = model_name
                        logging.info(
                            "Gemini success action=%s key_idx=%s model=%s attempts=%s",
                            action_name,
                            key_idx,
                            model_name,
                            attempt,
                        )
                        return result
                    except Exception as error:
                        status_code, error_type = _extract_error_details(error)
                        retryable = _is_retryable(error)
                        logging.warning(
                            "Gemini fail action=%s key_idx=%s model=%s attempt=%s "
                            "code=%s type=%s retryable=%s",
                            action_name,
                            key_idx,
                            model_name,
                            attempt,
                            status_code,
                            error_type,
                            retryable,
                        )
                        if error_type == "EmptyModelResponseError":
                            temporary_failure_only = False
                            hard_failures.append(error)
                            raise RuntimeError(
                                f"Gemini returned empty text response: {error}"
                            )
                        if retryable and attempt < self._max_retries_per_pair:
                            time.sleep(2 ** (attempt - 1))
                            continue
                        if not retryable:
                            temporary_failure_only = False
                            hard_failures.append(error)
                        break

        if temporary_failure_only:
            raise RuntimeError(self.GEMINI_LIMIT_EXHAUSTED_MESSAGE)
        if hard_failures:
            raise RuntimeError(
                f"All Gemini models failed. Last error: {hard_failures[-1]}"
            )
        raise RuntimeError("All Gemini models failed")
