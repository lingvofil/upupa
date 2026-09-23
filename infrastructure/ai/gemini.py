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
# google-genai HttpOptions.timeout is expressed in milliseconds. Keep each
# individual HTTP request below the process-wide 120s caller deadline so a
# broken transport eventually releases its provider worker.
GEMINI_HTTP_TIMEOUT_MS = 60_000

# Provider-wide 5xx failures usually follow the model rather than one API key.
# After a couple of such failures, temporarily bypass that model so one outage
# cannot consume the entire interactive AI deadline across the key pool.
MODEL_TRANSIENT_STATUS_CODES = frozenset({500, 502, 503, 504})
MODEL_CIRCUIT_FAILURE_THRESHOLD = 2
MODEL_CIRCUIT_COOLDOWNS_SECONDS = (60.0, 300.0, 900.0)

# A single 429 can be key/project-specific, so keep rotating keys first. If the
# same model rate-limits several distinct keys in one fallback pass, treat that
# as model/quota pressure and stop multiplying requests. Small key pools use
# full-pool exhaustion automatically.
MODEL_RATE_LIMIT_DISTINCT_KEY_THRESHOLD = 3
MODEL_RATE_LIMIT_CIRCUIT_COOLDOWNS_SECONDS = (30.0, 120.0, 300.0)

_last_call_ts: dict[str, float] = {}
_throttle_lock = threading.Lock()
_genai_lock = threading.RLock()
_client_cache: dict[str, genai.Client] = {}


def create_gemini_client(api_key: str) -> genai.Client:
    """Build one Gemini SDK client with an explicit transport timeout."""
    return genai.Client(
        api_key=api_key,
        http_options=genai_types.HttpOptions(timeout=GEMINI_HTTP_TIMEOUT_MS),
    )


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


def _is_rate_limit_error(error: Exception) -> bool:
    status_code, error_type = _extract_error_details(error)
    text = str(error).lower()
    if status_code == 429:
        return True
    if error_type in ("ResourceExhausted", "QuotaExceeded"):
        return True
    return any(
        marker in text
        for marker in ("429", "resourceexhausted", "quotaexceeded")
    )


def _is_retryable(error: Exception) -> bool:
    status_code, error_type = _extract_error_details(error)
    text = str(error).lower()
    if error_type == "EmptyModelResponseError":
        return False
    if _is_rate_limit_error(error) or status_code in MODEL_TRANSIENT_STATUS_CODES:
        return True
    return any(
        marker in text
        for marker in (
            "500",
            "502",
            "503",
            "504",
        )
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
    details = ["no candidate text"]
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        details.append("candidates=0")

    prompt_feedback = getattr(response, "prompt_feedback", None)
    if prompt_feedback is not None:
        block_reason = getattr(prompt_feedback, "block_reason", None)
        if block_reason:
            details.append(f"prompt_block_reason={block_reason}")
        safety = getattr(prompt_feedback, "safety_ratings", None)
        if safety:
            details.append(f"prompt_safety_ratings={safety}")

    for index, candidate in enumerate(candidates):
        finish_reason = getattr(candidate, "finish_reason", None)
        if finish_reason:
            details.append(f"candidate_{index}_finish_reason={finish_reason}")
        finish_message = getattr(candidate, "finish_message", None)
        if finish_message:
            details.append(f"candidate_{index}_finish_message={finish_message}")
        safety = getattr(candidate, "safety_ratings", None)
        if safety:
            details.append(f"candidate_{index}_safety_ratings={safety}")

    return "; ".join(details) or "no candidate text"


def _get_client(api_key: str) -> genai.Client:
    with _genai_lock:
        client = _client_cache.get(api_key)
        if client is None:
            client = create_gemini_client(api_key)
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
            require_text=True,
            model_queue=self.model_queue,
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
        self._model_health_lock = threading.Lock()
        self._model_transient_failures: dict[str, int] = {}
        self._model_circuit_until: dict[str, float] = {}
        self._model_circuit_level: dict[str, int] = {}
        self._model_rate_limit_circuit_until: dict[str, float] = {}
        self._model_rate_limit_circuit_level: dict[str, int] = {}
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
        require_text: bool = True,
        model_queue: Optional[List[str]] = None,
        **kwargs,
    ):
        return self._run_with_fallback(
            action_name="generate_content",
            chat_id=chat_id,
            request_fn=lambda model_obj: model_obj.generate_content(prompt, **kwargs),
            require_text=require_text,
            model_queue=model_queue,
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

    def _model_circuit_remaining(self, model_name: str) -> float:
        now = time.monotonic()
        with self._model_health_lock:
            until = self._model_circuit_until.get(model_name, 0.0)
            if until <= now:
                self._model_circuit_until.pop(model_name, None)
                self._model_transient_failures.pop(model_name, None)
                return 0.0
            return until - now

    def _model_rate_limit_circuit_remaining(self, model_name: str) -> float:
        now = time.monotonic()
        with self._model_health_lock:
            until = self._model_rate_limit_circuit_until.get(model_name, 0.0)
            if until <= now:
                self._model_rate_limit_circuit_until.pop(model_name, None)
                return 0.0
            return until - now

    def _record_model_success(self, model_name: str) -> None:
        with self._model_health_lock:
            self._model_transient_failures.pop(model_name, None)
            self._model_circuit_until.pop(model_name, None)
            self._model_circuit_level.pop(model_name, None)
            self._model_rate_limit_circuit_until.pop(model_name, None)
            self._model_rate_limit_circuit_level.pop(model_name, None)

    def _record_model_rate_limit_failure(
        self,
        model_name: str,
        *,
        distinct_keys: int,
        pool_size: int,
    ) -> bool:
        threshold = min(MODEL_RATE_LIMIT_DISTINCT_KEY_THRESHOLD, pool_size)
        now = time.monotonic()
        with self._model_health_lock:
            active_until = self._model_rate_limit_circuit_until.get(model_name, 0.0)
            if active_until > now:
                return True
            if active_until:
                self._model_rate_limit_circuit_until.pop(model_name, None)

            if distinct_keys < threshold:
                return False

            previous_level = self._model_rate_limit_circuit_level.get(model_name, 0)
            level = min(
                previous_level + 1,
                len(MODEL_RATE_LIMIT_CIRCUIT_COOLDOWNS_SECONDS),
            )
            cooldown = MODEL_RATE_LIMIT_CIRCUIT_COOLDOWNS_SECONDS[level - 1]
            self._model_rate_limit_circuit_level[model_name] = level
            self._model_rate_limit_circuit_until[model_name] = now + cooldown
            reason = "full_pool" if distinct_keys >= pool_size else "mass"

        logging.warning(
            "Gemini model 429 circuit opened model=%s distinct_keys=%s "
            "pool_size=%s reason=%s level=%s cooldown_s=%.0f",
            model_name,
            distinct_keys,
            pool_size,
            reason,
            level,
            cooldown,
        )
        return True

    def _record_model_transient_failure(self, model_name: str) -> bool:
        with self._model_health_lock:
            failures = self._model_transient_failures.get(model_name, 0) + 1
            self._model_transient_failures[model_name] = failures
            if failures < MODEL_CIRCUIT_FAILURE_THRESHOLD:
                return False

            previous_level = self._model_circuit_level.get(model_name, 0)
            level = min(previous_level + 1, len(MODEL_CIRCUIT_COOLDOWNS_SECONDS))
            cooldown = MODEL_CIRCUIT_COOLDOWNS_SECONDS[level - 1]
            self._model_circuit_level[model_name] = level
            self._model_circuit_until[model_name] = time.monotonic() + cooldown
            self._model_transient_failures.pop(model_name, None)

        logging.warning(
            "Gemini model circuit opened model=%s failures=%s level=%s "
            "cooldown_s=%.0f",
            model_name,
            failures,
            level,
            cooldown,
        )
        return True

    def _run_with_fallback(
        self,
        action_name: str,
        chat_id: Optional[int],
        request_fn: Callable,
        require_text: bool = False,
        model_queue: Optional[List[str]] = None,
    ):
        selected_queue = model_queue if model_queue is not None else self._get_queue(chat_id)
        model_queue = [
            self._normalize_model_name(name)
            for name in selected_queue
        ]
        key_indices = self._iter_key_indices()
        if not key_indices:
            raise RuntimeError("Gemini API keys pool is empty")

        hard_failures: List[Exception] = []
        temporary_failure_only = True

        for model_name in model_queue:
            rate_limit_circuit_remaining = self._model_rate_limit_circuit_remaining(
                model_name
            )
            if rate_limit_circuit_remaining > 0:
                logging.warning(
                    "Gemini skip model action=%s model=%s "
                    "rate_limit_circuit_remaining_s=%.1f",
                    action_name,
                    model_name,
                    rate_limit_circuit_remaining,
                )
                continue

            circuit_remaining = self._model_circuit_remaining(model_name)
            if circuit_remaining > 0:
                logging.warning(
                    "Gemini skip model action=%s model=%s circuit_remaining_s=%.1f",
                    action_name,
                    model_name,
                    circuit_remaining,
                )
                continue

            skip_model = False
            rate_limited_key_indices: set[int] = set()
            for key_idx in key_indices:
                api_key = self.keys_pool[key_idx]
                try:
                    _throttle_key(api_key)
                    model_obj = self._build_model(api_key, model_name)
                    result = request_fn(model_obj)
                    if require_text and not _extract_response_text(result).strip():
                        raise EmptyModelResponseError(
                            _empty_response_details(result)
                        )
                    self._record_model_success(model_name)
                    self.last_used_model_name = model_name
                    logging.info(
                        "Gemini success action=%s key_idx=%s model=%s attempts=1",
                        action_name,
                        key_idx,
                        model_name,
                    )
                    return result
                except Exception as error:
                    status_code, error_type = _extract_error_details(error)
                    retryable = _is_retryable(error)
                    logging.warning(
                        "Gemini fail action=%s key_idx=%s model=%s attempt=1 "
                        "code=%s type=%s retryable=%s",
                        action_name,
                        key_idx,
                        model_name,
                        status_code,
                        error_type,
                        retryable,
                    )

                    if error_type == "EmptyModelResponseError":
                        temporary_failure_only = False
                        hard_failures.append(error)
                        logging.warning(
                            "Gemini empty text action=%s model=%s details=%s; "
                            "trying next model",
                            action_name,
                            model_name,
                            error,
                        )
                        skip_model = True
                        break

                    if retryable:
                        # Keep rotating after an isolated 429 because one key/project
                        # may be exhausted while the next key still works. Once enough
                        # distinct keys reject the same model, open a shorter model-level
                        # rate-limit circuit and stop amplifying the quota storm.
                        if _is_rate_limit_error(error):
                            rate_limited_key_indices.add(key_idx)
                            if self._record_model_rate_limit_failure(
                                model_name,
                                distinct_keys=len(rate_limited_key_indices),
                                pool_size=len(key_indices),
                            ):
                                skip_model = True
                                break
                            continue

                        # 5xx service failures are commonly model-wide; two such
                        # failures open the existing circuit and immediately move
                        # to the next fallback model.
                        if status_code in MODEL_TRANSIENT_STATUS_CODES:
                            if self._record_model_transient_failure(model_name):
                                skip_model = True
                                break
                        continue

                    temporary_failure_only = False
                    hard_failures.append(error)

                    # A missing model will not become available by rotating keys.
                    if status_code == 404:
                        logging.warning(
                            "Gemini model unavailable action=%s model=%s; "
                            "trying next model",
                            action_name,
                            model_name,
                        )
                        skip_model = True
                        break

                    # Other hard failures can still be credential-specific.
                    # Try the next key before abandoning this model.

            if skip_model:
                continue

        if temporary_failure_only:
            raise RuntimeError(self.GEMINI_LIMIT_EXHAUSTED_MESSAGE)
        if hard_failures:
            raise RuntimeError(
                f"All Gemini models failed. Last error: {hard_failures[-1]}"
            )
        raise RuntimeError("All Gemini models failed")


__all__ = [
    "GEMINI_HTTP_TIMEOUT_MS",
    "GeminiModel",
    "ModelFallbackWrapper",
    "create_gemini_client",
]
