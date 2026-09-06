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
            self._record_request_latency(time.monotonic() - started)
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
            self._record_request_latency(time.monotonic() - started)
            raise AIRequestTimeoutError(
                f"AI request timeout before provider execution: {operation}"
            )

        try:
            result = future.result(timeout=remaining)
        except FutureTimeoutError as exc:
            self._record_request_timeout()
            self._record_request_latency(time.monotonic() - started)
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
            self._record_request_latency(time.monotonic() - started)
            self._logger.exception(
                "AI provider error operation=%s lane=%s queue_wait_ms=%d",
                operation,
                lane,
                round(queue_wait * 1000),
            )
            raise

        request_latency = time.monotonic() - started
        self._record_request_latency(request_latency)
        self._logger.info(
            "AI provider success operation=%s lane=%s queue_wait_ms=%d duration_ms=%d",
            operation,
            lane,
            round(queue_wait * 1000),
            round(request_latency * 1000),
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
    "AIExecutionSnapshot",
    "AILane",
    "AIQueueTimeoutError",
    "AIRequestTimeoutError",
    "ai_execution_lane",
    "get_ai_execution_snapshot",
    "run_ai_provider_call",
]
