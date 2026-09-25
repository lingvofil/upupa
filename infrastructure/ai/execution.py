"""Shared execution limits, deadlines and process-local AI metrics."""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from math import ceil
from typing import Any, Callable, Iterator, Literal

from core.settings import (
    AI_BACKGROUND_MAX_CONCURRENCY,
    AI_MAX_CONCURRENCY,
    AI_QUEUE_TIMEOUT_SECONDS,
    AI_REQUEST_TIMEOUT_SECONDS,
)


AILane = Literal["interactive", "background"]
_CURRENT_AI_LANE: contextvars.ContextVar[AILane] = contextvars.ContextVar(
    "upupa_ai_lane",
    default="interactive",
)
_CURRENT_AI_FEATURE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "upupa_ai_feature",
    default=None,
)


@dataclass(frozen=True)
class AIRequestContext:
    chat_id: int | None = None
    user_id: int | None = None
    chat_title: str | None = None
    user_name: str | None = None
    user_username: str | None = None


_CURRENT_AI_REQUEST_CONTEXT: contextvars.ContextVar[AIRequestContext] = contextvars.ContextVar(
    "upupa_ai_request_context",
    default=AIRequestContext(),
)

AIUsageRecorder = Callable[..., None]
_AI_USAGE_RECORDER: AIUsageRecorder | None = None


def configure_ai_usage_recorder(recorder: AIUsageRecorder | None) -> None:
    """Inject the application-level persistence callback without reversing layers."""
    global _AI_USAGE_RECORDER
    _AI_USAGE_RECORDER = recorder


@contextmanager
def ai_feature_context(feature: str) -> Iterator[None]:
    """Attach a human-readable Upupa feature/command to nested provider calls."""
    normalized = str(feature or "").strip()
    if not normalized:
        raise ValueError("AI feature name must not be empty")
    token = _CURRENT_AI_FEATURE.set(normalized)
    try:
        yield
    finally:
        _CURRENT_AI_FEATURE.reset(token)


@contextmanager
def ai_request_context(
    *,
    chat_id: int | None = None,
    user_id: int | None = None,
    chat_title: str | None = None,
    user_name: str | None = None,
    user_username: str | None = None,
) -> Iterator[None]:
    """Attach Telegram actor metadata to nested AI provider calls."""
    token = _CURRENT_AI_REQUEST_CONTEXT.set(
        AIRequestContext(
            chat_id=chat_id,
            user_id=user_id,
            chat_title=chat_title,
            user_name=user_name,
            user_username=user_username,
        )
    )
    try:
        yield
    finally:
        _CURRENT_AI_REQUEST_CONTEXT.reset(token)


class TokenTrackedText(str):
    """String-compatible provider result carrying token usage metadata."""

    def __new__(
        cls,
        value: str,
        *,
        model_name: str | None = None,
        usage: Any = None,
    ):
        instance = super().__new__(cls, value or "")
        instance._upupa_model_name = model_name
        instance._upupa_usage = usage
        return instance


def _usage_value(source: Any, *names: str) -> int | None:
    if source is None:
        return None
    for name in names:
        value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_known(*values: int | None) -> int | None:
    for value in values:
        if value is not None:
            return value
    return None


def _extract_token_usage(result: Any) -> dict[str, int | None]:
    usage = getattr(result, "_upupa_usage", None)
    if usage is None:
        usage = getattr(result, "usage_metadata", None)
    if usage is None:
        usage = getattr(result, "usage", None)

    prompt_details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "prompt_tokens_details", None)
    ) if usage is not None else None
    completion_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "completion_tokens_details", None)
    ) if usage is not None else None

    return {
        "input_tokens": _usage_value(
            usage,
            "prompt_token_count",
            "prompt_tokens",
            "input_tokens",
        ),
        "output_tokens": _usage_value(
            usage,
            "candidates_token_count",
            "completion_tokens",
            "output_tokens",
        ),
        "cached_tokens": _first_known(
            _usage_value(usage, "cached_content_token_count", "cached_tokens"),
            _usage_value(prompt_details, "cached_tokens"),
        ),
        "reasoning_tokens": _first_known(
            _usage_value(usage, "thoughts_token_count", "reasoning_tokens"),
            _usage_value(completion_details, "reasoning_tokens"),
        ),
        "total_tokens": _usage_value(
            usage,
            "total_token_count",
            "total_tokens",
        ),
    }


def _extract_model_name(result: Any) -> str | None:
    for name in ("_upupa_model_name", "model_version", "model"):
        value = getattr(result, name, None)
        if value:
            return str(value).removeprefix("models/")
    return None


def _provider_from_operation(operation: str) -> str:
    prefix = operation.split(".", 1)[0]
    return {
        "model": "gemini",
        "gemini_client": "gemini",
        "groq_ai": "groq",
        "gigachat_model": "gigachat",
        "gigachat": "gigachat",
        "openrouter_ai": "openrouter",
        "siliconflow_ai": "siliconflow",
    }.get(prefix, prefix)


def _record_ai_usage(
    *,
    operation: str,
    result: Any = None,
    success: bool,
    duration_ms: int,
    lane: AILane,
    context: AIRequestContext,
    explicit_chat_id: Any = None,
) -> None:
    """Persist one provider call without letting telemetry break generation."""
    recorder = _AI_USAGE_RECORDER
    if recorder is None:
        return

    try:
        usage = _extract_token_usage(result)
        chat_id = context.chat_id
        if chat_id is None and explicit_chat_id is not None:
            try:
                chat_id = int(explicit_chat_id)
            except (TypeError, ValueError):
                chat_id = None

        recorder(
            chat_id,
            context.user_id,
            _extract_model_name(result) or "unknown",
            operation,
            provider=_provider_from_operation(operation),
            feature=_CURRENT_AI_FEATURE.get(),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cached_tokens=usage["cached_tokens"],
            reasoning_tokens=usage["reasoning_tokens"],
            total_tokens=usage["total_tokens"],
            duration_ms=duration_ms,
            success=success,
            lane=lane,
            chat_title=context.chat_title,
            user_name=context.user_name,
            user_username=context.user_username,
        )
    except Exception:
        logging.getLogger(__name__).exception("Failed to persist AI usage telemetry")


class AIExecutionError(RuntimeError):
    """Base error for execution-governor failures."""


class AIQueueTimeoutError(AIExecutionError):
    """No provider slot became available before the queue deadline."""


class AIRequestTimeoutError(AIExecutionError):
    """A provider call exceeded the end-to-end request deadline."""


@dataclass(frozen=True)
class AIExecutionSnapshot:
    waiting: int
    waiting_background: int
    in_flight: int
    in_flight_background: int
    completed: int
    errors: int
    queue_timeouts: int
    request_timeouts: int
    queue_samples: int
    queue_wait_avg_ms: int
    queue_wait_p95_ms: int
    request_samples: int
    request_latency_avg_ms: int
    request_latency_p95_ms: int
    provider_samples: int
    provider_latency_avg_ms: int
    provider_latency_p95_ms: int


@contextmanager
def ai_execution_lane(lane: AILane) -> Iterator[None]:
    """Mark nested provider calls as interactive or background work."""
    if lane not in ("interactive", "background"):
        raise ValueError(f"Unsupported AI execution lane: {lane}")
    token = _CURRENT_AI_LANE.set(lane)
    try:
        yield
    finally:
        _CURRENT_AI_LANE.reset(token)


def _average_ms(samples: list[float]) -> int:
    if not samples:
        return 0
    return round(sum(samples) / len(samples))


def _p95_ms(samples: list[float]) -> int:
    if not samples:
        return 0
    ordered = sorted(samples)
    index = max(0, ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index])


class AIExecutionGovernor:
    """Bound provider concurrency and keep timed-out SDK calls from multiplying.

    Provider SDKs are synchronous in the current architecture. The caller can
    already live in ``asyncio.to_thread``; this governor adds one dedicated,
    bounded provider executor behind that boundary. A timed-out SDK call may
    keep its worker thread until the underlying library returns, but its slot
    is not released early, so repeated timeouts cannot create an unbounded
    pile of provider threads.

    Latency metrics are intentionally process-local and bounded. They retain a
    rolling window only, so diagnostics cannot grow memory usage over time.
    """

    def __init__(
        self,
        *,
        max_concurrency: int,
        background_max_concurrency: int,
        queue_timeout_seconds: float,
        request_timeout_seconds: float,
        latency_window_size: int = 256,
        logger: logging.Logger | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if background_max_concurrency < 1:
            raise ValueError("background_max_concurrency must be >= 1")
        if background_max_concurrency > max_concurrency:
            raise ValueError("background_max_concurrency cannot exceed max_concurrency")
        if queue_timeout_seconds <= 0 or request_timeout_seconds <= 0:
            raise ValueError("AI timeouts must be positive")
        if latency_window_size < 1:
            raise ValueError("latency_window_size must be >= 1")

        self.max_concurrency = max_concurrency
        self.background_max_concurrency = background_max_concurrency
        self.queue_timeout_seconds = float(queue_timeout_seconds)
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.latency_window_size = latency_window_size
        self._logger = logger or logging.getLogger(__name__)

        self._global_slots = threading.BoundedSemaphore(max_concurrency)
        self._background_slots = threading.BoundedSemaphore(background_max_concurrency)
        self._executor_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None

        self._state_lock = threading.Lock()
        self._waiting = 0
        self._waiting_background = 0
        self._in_flight = 0
        self._in_flight_background = 0
        self._completed = 0
        self._errors = 0
        self._queue_timeouts = 0
        self._request_timeouts = 0
        self._queue_wait_ms: deque[float] = deque(maxlen=latency_window_size)
        self._request_latency_ms: deque[float] = deque(maxlen=latency_window_size)
        self._provider_latency_ms: deque[float] = deque(maxlen=latency_window_size)

    def _get_executor(self) -> ThreadPoolExecutor:
        executor = self._executor
        if executor is not None:
            return executor
        with self._executor_lock:
            executor = self._executor
            if executor is None:
                executor = ThreadPoolExecutor(
                    max_workers=self.max_concurrency,
                    thread_name_prefix="upupa-ai",
                )
                self._executor = executor
            return executor

    def snapshot(self) -> AIExecutionSnapshot:
        with self._state_lock:
            queue_samples = list(self._queue_wait_ms)
            request_samples = list(self._request_latency_ms)
            provider_samples = list(self._provider_latency_ms)
            return AIExecutionSnapshot(
                waiting=self._waiting,
                waiting_background=self._waiting_background,
                in_flight=self._in_flight,
                in_flight_background=self._in_flight_background,
                completed=self._completed,
                errors=self._errors,
                queue_timeouts=self._queue_timeouts,
                request_timeouts=self._request_timeouts,
                queue_samples=len(queue_samples),
                queue_wait_avg_ms=_average_ms(queue_samples),
                queue_wait_p95_ms=_p95_ms(queue_samples),
                request_samples=len(request_samples),
                request_latency_avg_ms=_average_ms(request_samples),
                request_latency_p95_ms=_p95_ms(request_samples),
                provider_samples=len(provider_samples),
                provider_latency_avg_ms=_average_ms(provider_samples),
                provider_latency_p95_ms=_p95_ms(provider_samples),
            )

    def _change_waiting(self, lane: AILane, delta: int) -> None:
        with self._state_lock:
            self._waiting += delta
            if lane == "background":
                self._waiting_background += delta

    def _change_in_flight(self, lane: AILane, delta: int) -> None:
        with self._state_lock:
            self._in_flight += delta
            if lane == "background":
                self._in_flight_background += delta

    def _record_completion(self, *, error: bool) -> None:
        with self._state_lock:
            self._completed += 1
            if error:
                self._errors += 1

    def _record_queue_timeout(self) -> None:
        with self._state_lock:
            self._queue_timeouts += 1

    def _record_request_timeout(self) -> None:
        with self._state_lock:
            self._request_timeouts += 1

    def _record_queue_wait(self, elapsed_seconds: float) -> None:
        with self._state_lock:
            self._queue_wait_ms.append(max(0.0, elapsed_seconds * 1000))

    def _record_request_latency(self, elapsed_seconds: float) -> None:
        with self._state_lock:
            self._request_latency_ms.append(max(0.0, elapsed_seconds * 1000))

    def _record_provider_latency(self, elapsed_seconds: float) -> None:
        with self._state_lock:
            self._provider_latency_ms.append(max(0.0, elapsed_seconds * 1000))

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(0.0, deadline - time.monotonic())

    def _acquire_slots(self, lane: AILane, deadline: float, queue_deadline: float) -> tuple[bool, bool]:
        background_acquired = False
        global_acquired = False
        self._change_waiting(lane, 1)
        try:
            if lane == "background":
                remaining = min(self._remaining(deadline), self._remaining(queue_deadline))
                if remaining <= 0 or not self._background_slots.acquire(timeout=remaining):
                    raise AIQueueTimeoutError("AI background queue timeout")
                background_acquired = True

            remaining = min(self._remaining(deadline), self._remaining(queue_deadline))
            if remaining <= 0 or not self._global_slots.acquire(timeout=remaining):
                raise AIQueueTimeoutError("AI provider queue timeout")
            global_acquired = True
            return global_acquired, background_acquired
        except AIQueueTimeoutError:
            if background_acquired:
                self._background_slots.release()
            self._record_queue_timeout()
            raise
        finally:
            self._change_waiting(lane, -1)

    def run(
        self,
        operation: str,
        func: Callable[..., Any],
        /,
        *args: Any,
        timeout_seconds: float | None = None,
        queue_timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> Any:
        lane = _CURRENT_AI_LANE.get()
        request_context = _CURRENT_AI_REQUEST_CONTEXT.get()
        explicit_chat_id = kwargs.get("chat_id")
        started = time.monotonic()
        total_timeout = float(timeout_seconds or self.request_timeout_seconds)
        queue_timeout = float(queue_timeout_seconds or self.queue_timeout_seconds)
        deadline = started + total_timeout
        queue_deadline = started + min(queue_timeout, total_timeout)

        try:
            global_acquired, background_acquired = self._acquire_slots(
                lane,
                deadline,
                queue_deadline,
            )
        except AIQueueTimeoutError:
            elapsed = time.monotonic() - started
            self._record_queue_wait(elapsed)
            self._record_request_latency(elapsed)
            self._logger.warning(
                "AI queue timeout operation=%s lane=%s wait_ms=%d timeout_s=%.1f",
                operation,
                lane,
                round(elapsed * 1000),
                min(queue_timeout, total_timeout),
            )
            _record_ai_usage(
                operation=operation,
                success=False,
                duration_ms=round(elapsed * 1000),
                lane=lane,
                context=request_context,
                explicit_chat_id=explicit_chat_id,
            )
            raise

        queue_wait = time.monotonic() - started
        self._record_queue_wait(queue_wait)
        self._change_in_flight(lane, 1)
        provider_started = time.monotonic()

        try:
            future: Future[Any] = self._get_executor().submit(func, *args, **kwargs)
        except Exception:
            self._change_in_flight(lane, -1)
            if global_acquired:
                self._global_slots.release()
            if background_acquired:
                self._background_slots.release()
            self._record_completion(error=True)
            elapsed = time.monotonic() - started
            self._record_request_latency(elapsed)
            _record_ai_usage(
                operation=operation,
                success=False,
                duration_ms=round(elapsed * 1000),
                lane=lane,
                context=request_context,
                explicit_chat_id=explicit_chat_id,
            )
            raise

        released = False
        release_lock = threading.Lock()

        def release_slots(done: Future[Any]) -> None:
            nonlocal released
            with release_lock:
                if released:
                    return
                released = True
            self._record_provider_latency(time.monotonic() - provider_started)
            self._change_in_flight(lane, -1)
            if global_acquired:
                self._global_slots.release()
            if background_acquired:
                self._background_slots.release()
            try:
                error = done.exception() is not None
            except BaseException:
                error = True
            self._record_completion(error=error)

        future.add_done_callback(release_slots)

        remaining = self._remaining(deadline)
        if remaining <= 0:
            self._record_request_timeout()
            elapsed = time.monotonic() - started
            self._record_request_latency(elapsed)
            _record_ai_usage(
                operation=operation,
                success=False,
                duration_ms=round(elapsed * 1000),
                lane=lane,
                context=request_context,
                explicit_chat_id=explicit_chat_id,
            )
            raise AIRequestTimeoutError(
                f"AI request timeout before provider execution: {operation}"
            )

        try:
            result = future.result(timeout=remaining)
        except FutureTimeoutError as exc:
            self._record_request_timeout()
            elapsed = time.monotonic() - started
            self._record_request_latency(elapsed)
            _record_ai_usage(
                operation=operation,
                success=False,
                duration_ms=round(elapsed * 1000),
                lane=lane,
                context=request_context,
                explicit_chat_id=explicit_chat_id,
            )
            self._logger.warning(
                "AI request timeout operation=%s lane=%s queue_wait_ms=%d timeout_s=%.1f",
                operation,
                lane,
                round(queue_wait * 1000),
                total_timeout,
            )
            raise AIRequestTimeoutError(
                f"AI request timed out after {total_timeout:.1f}s: {operation}"
            ) from exc
        except Exception:
            elapsed = time.monotonic() - started
            self._record_request_latency(elapsed)
            _record_ai_usage(
                operation=operation,
                success=False,
                duration_ms=round(elapsed * 1000),
                lane=lane,
                context=request_context,
                explicit_chat_id=explicit_chat_id,
            )
            self._logger.exception(
                "AI provider error operation=%s lane=%s queue_wait_ms=%d",
                operation,
                lane,
                round(queue_wait * 1000),
            )
            raise

        request_latency = time.monotonic() - started
        self._record_request_latency(request_latency)
        duration_ms = round(request_latency * 1000)
        self._logger.info(
            "AI provider success operation=%s lane=%s queue_wait_ms=%d duration_ms=%d",
            operation,
            lane,
            round(queue_wait * 1000),
            duration_ms,
        )
        _record_ai_usage(
            operation=operation,
            result=result,
            success=True,
            duration_ms=duration_ms,
            lane=lane,
            context=request_context,
            explicit_chat_id=explicit_chat_id,
        )
        return result

    def shutdown(self, *, wait: bool = True) -> None:
        with self._executor_lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=False)


_default_governor = AIExecutionGovernor(
    max_concurrency=AI_MAX_CONCURRENCY,
    background_max_concurrency=AI_BACKGROUND_MAX_CONCURRENCY,
    queue_timeout_seconds=AI_QUEUE_TIMEOUT_SECONDS,
    request_timeout_seconds=AI_REQUEST_TIMEOUT_SECONDS,
)


def run_ai_provider_call(
    operation: str,
    func: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run one configured provider operation through the process-wide governor."""
    return _default_governor.run(operation, func, *args, **kwargs)


def get_ai_execution_snapshot() -> AIExecutionSnapshot:
    return _default_governor.snapshot()


__all__ = [
    "AIExecutionError",
    "AIExecutionGovernor",
    "AIRequestContext",
    "AIExecutionSnapshot",
    "AILane",
    "AIQueueTimeoutError",
    "AIRequestTimeoutError",
    "TokenTrackedText",
    "configure_ai_usage_recorder",
    "ai_execution_lane",
    "ai_request_context",
    "get_ai_execution_snapshot",
    "run_ai_provider_call",
]
