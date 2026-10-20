"""Shared execution limits, deadlines and process-local AI metrics."""

from __future__ import annotations

import asyncio
import contextvars
from functools import partial, wraps
import inspect
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
    AI_MEDIA_MAX_CONCURRENCY,
    AI_QUEUE_TIMEOUT_SECONDS,
    AI_REQUEST_TIMEOUT_SECONDS,
)


AILane = Literal["interactive", "background"]
# Capacity pools: "text" covers dialogue/DnD/background text, "media" covers
# image and video generation, which runs for minutes and must not hold text slots.
AIPool = Literal["text", "media"]
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
def ai_feature_context(feature: str, *, only_if_unset: bool = False) -> Iterator[None]:
    """Attach a human-readable Upupa feature/command to nested provider calls."""
    normalized = str(feature or "").strip()
    if not normalized:
        raise ValueError("AI feature name must not be empty")
    if only_if_unset and _CURRENT_AI_FEATURE.get():
        yield
        return
    token = _CURRENT_AI_FEATURE.set(normalized)
    try:
        yield
    finally:
        _CURRENT_AI_FEATURE.reset(token)


def ai_feature(feature: str, *, only_if_unset: bool = False):
    """Decorate a sync or async feature entry point with token attribution."""

    def decorator(func):
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                with ai_feature_context(feature, only_if_unset=only_if_unset):
                    return await func(*args, **kwargs)

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            with ai_feature_context(feature, only_if_unset=only_if_unset):
                return func(*args, **kwargs)

        return sync_wrapper

    return decorator


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
    if operation.startswith("dnd.gemini."):
        return "gemini"
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
    feature: str | None,
    context: AIRequestContext,
    explicit_chat_id: Any = None,
    requested_model: str | None = None,
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
            _extract_model_name(result) or requested_model or "unknown",
            operation,
            provider=_provider_from_operation(operation),
            feature=feature,
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
    """Process-local governor state.

    Unprefixed fields describe the text pool (dialogue, DnD, background text);
    ``media_*`` fields describe the separate image/video lane.
    """

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
    media_max_concurrency: int
    media_waiting: int
    media_in_flight: int
    media_completed: int
    media_errors: int
    media_queue_timeouts: int
    media_request_timeouts: int
    media_queue_samples: int
    media_queue_wait_avg_ms: int
    media_queue_wait_p95_ms: int
    media_request_samples: int
    media_request_latency_avg_ms: int
    media_request_latency_p95_ms: int
    media_provider_samples: int
    media_provider_latency_avg_ms: int
    media_provider_latency_p95_ms: int


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


class _SlotWaiter:
    """One queued acquirer: either a blocked thread or a suspended coroutine."""

    __slots__ = ("event", "loop", "future", "granted")

    def __init__(
        self,
        *,
        event: threading.Event | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        future: asyncio.Future[None] | None = None,
    ) -> None:
        self.event = event
        self.loop = loop
        self.future = future
        self.granted = False

    def grant(self) -> bool:
        """Hand a released slot to this waiter; called with the pool lock held."""
        if self.event is not None:
            self.granted = True
            self.event.set()
            return True
        try:
            self.loop.call_soon_threadsafe(_resolve_waiter_future, self.future)
        except RuntimeError:
            # The waiter's event loop is closed, so it can never take the slot.
            return False
        self.granted = True
        return True


def _resolve_waiter_future(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


class SlotPool:
    """FIFO counting semaphore shared by threads and coroutines.

    Synchronous SDK callers block in ``acquire``. Event-loop callers await
    ``acquire_async``, which parks only a future, so a queued request does not
    pin a thread. A released slot goes straight to the oldest waiter, so
    neither kind of waiter can overtake the other.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._available = capacity
        self._lock = threading.Lock()
        self._waiters: deque[_SlotWaiter] = deque()

    def acquire(self, timeout: float | None = None) -> bool:
        with self._lock:
            if self._available:
                self._available -= 1
                return True
            if timeout is not None and timeout <= 0:
                return False
            waiter = _SlotWaiter(event=threading.Event())
            self._waiters.append(waiter)
        try:
            waiter.event.wait(timeout)
        except BaseException:
            if self._settle(waiter):
                self.release()
            raise
        return self._settle(waiter)

    async def acquire_async(self, timeout: float | None = None) -> bool:
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._available:
                self._available -= 1
                return True
            if timeout is not None and timeout <= 0:
                return False
            waiter = _SlotWaiter(loop=loop, future=loop.create_future())
            self._waiters.append(waiter)
        try:
            await asyncio.wait((waiter.future,), timeout=timeout)
        except BaseException:
            # Cancelled while queued: a slot granted meanwhile must not leak.
            if self._settle(waiter):
                self.release()
            raise
        return self._settle(waiter)

    def _settle(self, waiter: _SlotWaiter) -> bool:
        """Return True if the waiter owns a slot, otherwise drop it from the queue."""
        with self._lock:
            if waiter.granted:
                return True
            try:
                self._waiters.remove(waiter)
            except ValueError:
                pass
            return False

    def release(self) -> None:
        with self._lock:
            while self._waiters:
                if self._waiters.popleft().grant():
                    return
            if self._available >= self.capacity:
                raise ValueError("SlotPool released more times than acquired")
            self._available += 1

    def __enter__(self) -> SlotPool:
        self.acquire()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.release()

    async def __aenter__(self) -> SlotPool:
        await self.acquire_async()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.release()


class _PoolMetrics:
    """Counters and rolling latency windows of one capacity pool."""

    def __init__(self, window_size: int) -> None:
        self.waiting = 0
        self.in_flight = 0
        self.completed = 0
        self.errors = 0
        self.queue_timeouts = 0
        self.request_timeouts = 0
        self.queue_wait_ms: deque[float] = deque(maxlen=window_size)
        self.request_latency_ms: deque[float] = deque(maxlen=window_size)
        self.provider_latency_ms: deque[float] = deque(maxlen=window_size)

    def summary(self, prefix: str = "") -> dict[str, int]:
        queue_samples = list(self.queue_wait_ms)
        request_samples = list(self.request_latency_ms)
        provider_samples = list(self.provider_latency_ms)
        values = {
            "waiting": self.waiting,
            "in_flight": self.in_flight,
            "completed": self.completed,
            "errors": self.errors,
            "queue_timeouts": self.queue_timeouts,
            "request_timeouts": self.request_timeouts,
            "queue_samples": len(queue_samples),
            "queue_wait_avg_ms": _average_ms(queue_samples),
            "queue_wait_p95_ms": _p95_ms(queue_samples),
            "request_samples": len(request_samples),
            "request_latency_avg_ms": _average_ms(request_samples),
            "request_latency_p95_ms": _p95_ms(request_samples),
            "provider_samples": len(provider_samples),
            "provider_latency_avg_ms": _average_ms(provider_samples),
            "provider_latency_p95_ms": _p95_ms(provider_samples),
        }
        return {f"{prefix}{name}": value for name, value in values.items()}


@dataclass(frozen=True)
class _CallPlan:
    """Deadlines and usage attribution of one governed provider call."""

    pool: AIPool
    operation: str
    lane: AILane
    feature: str | None
    request_context: AIRequestContext
    explicit_chat_id: Any
    requested_model: str | None
    started: float
    total_timeout: float
    queue_timeout: float
    deadline: float
    queue_deadline: float


class AIExecutionGovernor:
    """Bound provider concurrency and keep timed-out SDK calls from multiplying.

    Provider SDKs are synchronous in the current architecture. The caller can
    already live in ``asyncio.to_thread``; this governor adds dedicated,
    bounded provider executors behind that boundary. A timed-out SDK call may
    keep its worker thread until the underlying library returns, but its slot
    is not released early, so repeated timeouts cannot create an unbounded
    pile of provider threads.

    Text and media work use separate pools. Image/video jobs run for minutes,
    so they get their own slots and executor: a burst of generations queues
    only behind other generations and never starves dialogue or DnD replies.
    Event-loop media callers use ``run_media_async``, which waits for a slot
    and for the provider without holding a default-executor thread.

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
        media_max_concurrency: int = 1,
        latency_window_size: int = 256,
        logger: logging.Logger | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if background_max_concurrency < 1:
            raise ValueError("background_max_concurrency must be >= 1")
        if background_max_concurrency > max_concurrency:
            raise ValueError("background_max_concurrency cannot exceed max_concurrency")
        if media_max_concurrency < 1:
            raise ValueError("media_max_concurrency must be >= 1")
        if queue_timeout_seconds <= 0 or request_timeout_seconds <= 0:
            raise ValueError("AI timeouts must be positive")
        if latency_window_size < 1:
            raise ValueError("latency_window_size must be >= 1")

        self.max_concurrency = max_concurrency
        self.background_max_concurrency = background_max_concurrency
        self.media_max_concurrency = media_max_concurrency
        self.queue_timeout_seconds = float(queue_timeout_seconds)
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.latency_window_size = latency_window_size
        self._logger = logger or logging.getLogger(__name__)

        self._global_slots = SlotPool(max_concurrency)
        self._background_slots = SlotPool(background_max_concurrency)
        self._media_slots = SlotPool(media_max_concurrency)
        self._executor_lock = threading.Lock()
        self._executors: dict[AIPool, ThreadPoolExecutor] = {}

        self._state_lock = threading.Lock()
        self._metrics: dict[AIPool, _PoolMetrics] = {
            "text": _PoolMetrics(latency_window_size),
            "media": _PoolMetrics(latency_window_size),
        }
        self._waiting_background = 0
        self._in_flight_background = 0

    def _get_executor(self, pool: AIPool) -> ThreadPoolExecutor:
        # One worker per slot: an admitted call never queues inside the executor.
        with self._executor_lock:
            executor = self._executors.get(pool)
            if executor is None:
                media = pool == "media"
                executor = ThreadPoolExecutor(
                    max_workers=self.media_max_concurrency if media else self.max_concurrency,
                    thread_name_prefix="upupa-ai-media" if media else "upupa-ai",
                )
                self._executors[pool] = executor
            return executor

    def snapshot(self) -> AIExecutionSnapshot:
        with self._state_lock:
            return AIExecutionSnapshot(
                waiting_background=self._waiting_background,
                in_flight_background=self._in_flight_background,
                media_max_concurrency=self.media_max_concurrency,
                **self._metrics["text"].summary(),
                **self._metrics["media"].summary("media_"),
            )

    def _change_waiting(self, plan: _CallPlan, delta: int) -> None:
        with self._state_lock:
            self._metrics[plan.pool].waiting += delta
            if plan.pool == "text" and plan.lane == "background":
                self._waiting_background += delta

    def _change_in_flight(self, plan: _CallPlan, delta: int) -> None:
        with self._state_lock:
            self._metrics[plan.pool].in_flight += delta
            if plan.pool == "text" and plan.lane == "background":
                self._in_flight_background += delta

    def _record_completion(self, plan: _CallPlan, *, error: bool) -> None:
        with self._state_lock:
            metrics = self._metrics[plan.pool]
            metrics.completed += 1
            if error:
                metrics.errors += 1

    def _record_queue_timeout(self, plan: _CallPlan) -> None:
        with self._state_lock:
            self._metrics[plan.pool].queue_timeouts += 1

    def _record_request_timeout(self, plan: _CallPlan) -> None:
        with self._state_lock:
            self._metrics[plan.pool].request_timeouts += 1

    def _record_queue_wait(self, plan: _CallPlan, elapsed_seconds: float) -> None:
        with self._state_lock:
            self._metrics[plan.pool].queue_wait_ms.append(max(0.0, elapsed_seconds * 1000))

    def _record_request_latency(self, plan: _CallPlan, elapsed_seconds: float) -> None:
        with self._state_lock:
            self._metrics[plan.pool].request_latency_ms.append(max(0.0, elapsed_seconds * 1000))

    def _record_provider_latency(self, plan: _CallPlan, elapsed_seconds: float) -> None:
        with self._state_lock:
            self._metrics[plan.pool].provider_latency_ms.append(max(0.0, elapsed_seconds * 1000))

    def _record_usage(
        self,
        plan: _CallPlan,
        *,
        success: bool,
        elapsed_seconds: float,
        result: Any = None,
    ) -> None:
        _record_ai_usage(
            operation=plan.operation,
            result=result,
            success=success,
            duration_ms=round(elapsed_seconds * 1000),
            lane=plan.lane,
            feature=plan.feature,
            context=plan.request_context,
            explicit_chat_id=plan.explicit_chat_id,
            requested_model=plan.requested_model,
        )

    @staticmethod
    def _remaining(deadline: float) -> float:
        return max(0.0, deadline - time.monotonic())

    def _queue_remaining(self, plan: _CallPlan) -> float:
        return min(self._remaining(plan.deadline), self._remaining(plan.queue_deadline))

    def _plan(
        self,
        pool: AIPool,
        operation: str,
        kwargs: dict[str, Any],
        *,
        timeout_seconds: float | None,
        queue_timeout_seconds: float | None,
        provider_deadline: float | None,
        requested_model: str | None,
    ) -> _CallPlan:
        started = time.monotonic()
        total_timeout = float(timeout_seconds or self.request_timeout_seconds)
        queue_timeout = min(float(queue_timeout_seconds or self.queue_timeout_seconds), total_timeout)
        deadline = started + total_timeout
        if provider_deadline is not None:
            deadline = min(deadline, float(provider_deadline))
        return _CallPlan(
            pool=pool,
            operation=operation,
            lane=_CURRENT_AI_LANE.get(),
            feature=_CURRENT_AI_FEATURE.get(),
            request_context=_CURRENT_AI_REQUEST_CONTEXT.get(),
            explicit_chat_id=kwargs.get("chat_id"),
            requested_model=requested_model or (
                str(kwargs["model"]).removeprefix("models/") if kwargs.get("model") else None
            ),
            started=started,
            total_timeout=total_timeout,
            queue_timeout=queue_timeout,
            deadline=deadline,
            queue_deadline=started + queue_timeout,
        )

    def _slot_gates(self, plan: _CallPlan) -> tuple[tuple[SlotPool, str], ...]:
        if plan.pool == "media":
            return ((self._media_slots, "media"),)
        if plan.lane == "background":
            return ((self._background_slots, "background"), (self._global_slots, "provider"))
        return ((self._global_slots, "provider"),)

    @staticmethod
    def _release_slots(acquired: list[SlotPool]) -> None:
        for slots in reversed(acquired):
            slots.release()

    def _acquire_slots(self, plan: _CallPlan) -> list[SlotPool]:
        acquired: list[SlotPool] = []
        self._change_waiting(plan, 1)
        try:
            for slots, label in self._slot_gates(plan):
                remaining = self._queue_remaining(plan)
                if remaining <= 0 or not slots.acquire(timeout=remaining):
                    raise AIQueueTimeoutError(f"AI {label} queue timeout")
                acquired.append(slots)
            return acquired
        except BaseException:
            self._release_slots(acquired)
            raise
        finally:
            self._change_waiting(plan, -1)

    async def _acquire_slots_async(self, plan: _CallPlan) -> list[SlotPool]:
        acquired: list[SlotPool] = []
        self._change_waiting(plan, 1)
        try:
            for slots, label in self._slot_gates(plan):
                remaining = self._queue_remaining(plan)
                if remaining <= 0 or not await slots.acquire_async(timeout=remaining):
                    raise AIQueueTimeoutError(f"AI {label} queue timeout")
                acquired.append(slots)
            return acquired
        except BaseException:
            self._release_slots(acquired)
            raise
        finally:
            self._change_waiting(plan, -1)

    def _dispatch(
        self,
        plan: _CallPlan,
        acquired: list[SlotPool],
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        provider_start_guard: Callable[[], None] | None,
    ) -> tuple[Future[Any], float]:
        """Submit an admitted call; its slots are freed when the worker really ends."""
        queue_wait = time.monotonic() - plan.started
        self._record_queue_wait(plan, queue_wait)
        self._change_in_flight(plan, 1)
        provider_started = time.monotonic()
        deadline = plan.deadline

        try:
            call_context = contextvars.copy_context()

            def guarded_provider_call():
                if self._remaining(deadline) <= 0:
                    raise AIRequestTimeoutError("AI request expired before provider dispatch")
                if provider_start_guard is not None:
                    provider_start_guard()
                return func(*args, **kwargs)

            future: Future[Any] = self._get_executor(plan.pool).submit(
                call_context.run,
                guarded_provider_call,
            )
        except Exception:
            self._change_in_flight(plan, -1)
            self._release_slots(acquired)
            self._record_completion(plan, error=True)
            raise

        released = False
        release_lock = threading.Lock()

        def release_slots(done: Future[Any]) -> None:
            nonlocal released
            with release_lock:
                if released:
                    return
                released = True
            self._record_provider_latency(plan, time.monotonic() - provider_started)
            self._change_in_flight(plan, -1)
            self._release_slots(acquired)
            try:
                error = done.exception() is not None
            except BaseException:
                error = True
            self._record_completion(plan, error=error)

        future.add_done_callback(release_slots)
        return future, queue_wait

    # The _finish_* helpers may write usage telemetry to SQLite, so the async
    # path runs them in a worker thread rather than on the event loop.

    def _finish_queue_timeout(self, plan: _CallPlan) -> None:
        elapsed = time.monotonic() - plan.started
        self._record_queue_timeout(plan)
        self._record_queue_wait(plan, elapsed)
        self._record_request_latency(plan, elapsed)
        self._logger.warning(
            "AI queue timeout operation=%s lane=%s pool=%s wait_ms=%d timeout_s=%.1f",
            plan.operation,
            plan.lane,
            plan.pool,
            round(elapsed * 1000),
            plan.queue_timeout,
        )
        self._record_usage(plan, success=False, elapsed_seconds=elapsed)

    def _finish_dispatch_failure(self, plan: _CallPlan) -> None:
        elapsed = time.monotonic() - plan.started
        self._record_request_latency(plan, elapsed)
        self._record_usage(plan, success=False, elapsed_seconds=elapsed)

    def _finish_request_timeout(
        self,
        plan: _CallPlan,
        queue_wait: float,
        *,
        before_dispatch: bool = False,
    ) -> AIRequestTimeoutError:
        elapsed = time.monotonic() - plan.started
        self._record_request_timeout(plan)
        self._record_request_latency(plan, elapsed)
        self._record_usage(plan, success=False, elapsed_seconds=elapsed)
        if before_dispatch:
            return AIRequestTimeoutError(
                f"AI request timeout before provider execution: {plan.operation}"
            )
        self._logger.warning(
            "AI request timeout operation=%s lane=%s pool=%s queue_wait_ms=%d timeout_s=%.1f",
            plan.operation,
            plan.lane,
            plan.pool,
            round(queue_wait * 1000),
            plan.total_timeout,
        )
        return AIRequestTimeoutError(
            f"AI request timed out after {plan.total_timeout:.1f}s: {plan.operation}"
        )

    def _finish_provider_error(
        self,
        plan: _CallPlan,
        queue_wait: float,
        error: BaseException,
    ) -> None:
        elapsed = time.monotonic() - plan.started
        self._record_request_latency(plan, elapsed)
        self._record_usage(plan, success=False, elapsed_seconds=elapsed)
        self._logger.error(
            "AI provider error operation=%s lane=%s pool=%s queue_wait_ms=%d",
            plan.operation,
            plan.lane,
            plan.pool,
            round(queue_wait * 1000),
            exc_info=error,
        )

    def _finish_success(self, plan: _CallPlan, queue_wait: float, result: Any) -> None:
        elapsed = time.monotonic() - plan.started
        self._record_request_latency(plan, elapsed)
        self._logger.info(
            "AI provider success operation=%s lane=%s pool=%s queue_wait_ms=%d duration_ms=%d",
            plan.operation,
            plan.lane,
            plan.pool,
            round(queue_wait * 1000),
            round(elapsed * 1000),
        )
        self._record_usage(plan, success=True, elapsed_seconds=elapsed, result=result)

    def _finish_detached(self, plan: _CallPlan, queue_wait: float, done: Future[Any]) -> None:
        """Record a call whose async caller was cancelled once its worker ends."""
        try:
            error = done.exception()
        except BaseException as cancelled:
            error = cancelled
        if error is None:
            self._finish_success(plan, queue_wait, done.result())
        else:
            self._finish_provider_error(plan, queue_wait, error)

    def _run_blocking(
        self,
        plan: _CallPlan,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        provider_start_guard: Callable[[], None] | None,
    ) -> Any:
        try:
            acquired = self._acquire_slots(plan)
        except AIQueueTimeoutError:
            self._finish_queue_timeout(plan)
            raise

        try:
            future, queue_wait = self._dispatch(
                plan, acquired, func, args, kwargs, provider_start_guard,
            )
        except Exception:
            self._finish_dispatch_failure(plan)
            raise

        remaining = self._remaining(plan.deadline)
        if remaining <= 0:
            raise self._finish_request_timeout(plan, queue_wait, before_dispatch=True)

        try:
            result = future.result(timeout=remaining)
        except FutureTimeoutError as exc:
            raise self._finish_request_timeout(plan, queue_wait) from exc
        except Exception as exc:
            self._finish_provider_error(plan, queue_wait, exc)
            raise

        self._finish_success(plan, queue_wait, result)
        return result

    async def _run_async(
        self,
        plan: _CallPlan,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        provider_start_guard: Callable[[], None] | None,
    ) -> Any:
        try:
            acquired = await self._acquire_slots_async(plan)
        except AIQueueTimeoutError:
            await asyncio.to_thread(self._finish_queue_timeout, plan)
            raise

        try:
            future, queue_wait = self._dispatch(
                plan, acquired, func, args, kwargs, provider_start_guard,
            )
        except Exception:
            await asyncio.to_thread(self._finish_dispatch_failure, plan)
            raise

        provider_result = asyncio.wrap_future(future)
        try:
            done, _pending = await asyncio.wait(
                (provider_result,),
                timeout=self._remaining(plan.deadline),
            )
        except asyncio.CancelledError:
            # Like a timed-out call, the worker keeps its slot until it ends;
            # its outcome is recorded from the worker thread at that point.
            provider_result.cancel()
            future.add_done_callback(partial(self._finish_detached, plan, queue_wait))
            raise

        if not done:
            provider_result.cancel()
            raise await asyncio.to_thread(self._finish_request_timeout, plan, queue_wait)

        try:
            result = provider_result.result()
        except Exception as exc:
            await asyncio.to_thread(self._finish_provider_error, plan, queue_wait, exc)
            raise

        await asyncio.to_thread(self._finish_success, plan, queue_wait, result)
        return result

    def run(
        self,
        operation: str,
        func: Callable[..., Any],
        /,
        *args: Any,
        timeout_seconds: float | None = None,
        queue_timeout_seconds: float | None = None,
        provider_deadline: float | None = None,
        provider_start_guard: Callable[[], None] | None = None,
        requested_model: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run a text provider call, blocking the calling thread."""
        plan = self._plan(
            "text",
            operation,
            kwargs,
            timeout_seconds=timeout_seconds,
            queue_timeout_seconds=queue_timeout_seconds,
            provider_deadline=provider_deadline,
            requested_model=requested_model,
        )
        return self._run_blocking(plan, func, args, kwargs, provider_start_guard)

    def run_media(
        self,
        operation: str,
        func: Callable[..., Any],
        /,
        *args: Any,
        timeout_seconds: float | None = None,
        queue_timeout_seconds: float | None = None,
        provider_deadline: float | None = None,
        provider_start_guard: Callable[[], None] | None = None,
        requested_model: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run an image/video call in the media lane, blocking the calling thread."""
        plan = self._plan(
            "media",
            operation,
            kwargs,
            timeout_seconds=timeout_seconds,
            queue_timeout_seconds=queue_timeout_seconds,
            provider_deadline=provider_deadline,
            requested_model=requested_model,
        )
        return self._run_blocking(plan, func, args, kwargs, provider_start_guard)

    async def run_media_async(
        self,
        operation: str,
        func: Callable[..., Any],
        /,
        *args: Any,
        timeout_seconds: float | None = None,
        queue_timeout_seconds: float | None = None,
        provider_deadline: float | None = None,
        provider_start_guard: Callable[[], None] | None = None,
        requested_model: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run an image/video call in the media lane from the event loop."""
        plan = self._plan(
            "media",
            operation,
            kwargs,
            timeout_seconds=timeout_seconds,
            queue_timeout_seconds=queue_timeout_seconds,
            provider_deadline=provider_deadline,
            requested_model=requested_model,
        )
        return await self._run_async(plan, func, args, kwargs, provider_start_guard)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._executor_lock:
            executors = list(self._executors.values())
            self._executors.clear()
        for executor in executors:
            executor.shutdown(wait=wait, cancel_futures=False)


_default_governor = AIExecutionGovernor(
    max_concurrency=AI_MAX_CONCURRENCY,
    background_max_concurrency=AI_BACKGROUND_MAX_CONCURRENCY,
    media_max_concurrency=AI_MEDIA_MAX_CONCURRENCY,
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


def run_ai_media_call_sync(
    operation: str,
    func: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run one image/video operation in the media lane from a worker thread."""
    return _default_governor.run_media(operation, func, *args, **kwargs)


async def run_ai_media_call(
    operation: str,
    func: Callable[..., Any],
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run one image/video operation in the media lane from the event loop."""
    return await _default_governor.run_media_async(operation, func, *args, **kwargs)


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
    "SlotPool",
    "TokenTrackedText",
    "configure_ai_usage_recorder",
    "ai_execution_lane",
    "ai_request_context",
    "get_ai_execution_snapshot",
    "run_ai_media_call",
    "run_ai_media_call_sync",
    "run_ai_provider_call",
]
