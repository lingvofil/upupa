"""Media generation has its own AI lane and does not starve text or the thread pool.

Audit 2026-10-20: three slow image/video generations held all three shared AI
slots, so the next chat reply failed with AIQueueTimeoutError, and every
queued call also pinned a default-executor thread.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace

import pytest

# Настраивает fake env и моки тяжёлых библиотек до импорта AI-модулей.
from tests import test_smoke_imports

del test_smoke_imports

from AI import aihorde_image, gigachat_image, media_execution
from app import bootstrap
from infrastructure.ai import execution
from infrastructure.ai.execution import (
    AIExecutionGovernor,
    AIQueueTimeoutError,
    SlotPool,
    ai_execution_lane,
)


def _governor(*, maximum=3, background=1, media=3, queue=0.05, request=2.0):
    return AIExecutionGovernor(
        max_concurrency=maximum,
        background_max_concurrency=background,
        media_max_concurrency=media,
        queue_timeout_seconds=queue,
        request_timeout_seconds=request,
    )


async def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not reached in time")
        await asyncio.sleep(0.005)


def test_three_slow_media_calls_do_not_block_text_reply():
    # Production text capacity: three shared slots.
    governor = _governor(maximum=3, media=3, queue=0.05)
    release = threading.Event()

    def slow_generation():
        release.wait(timeout=2)
        return b"image"

    async def scenario():
        media = [
            asyncio.create_task(governor.run_media_async(f"media-{index}", slow_generation))
            for index in range(3)
        ]
        try:
            await _wait_until(lambda: governor.snapshot().media_in_flight == 3)

            reply = await asyncio.to_thread(
                governor.run,
                "model.generate_content",
                lambda: "reply",
            )
            assert reply == "reply"

            # Completion is recorded by the worker's done-callback.
            await _wait_until(lambda: governor.snapshot().completed == 1)
            snapshot = governor.snapshot()
            assert snapshot.queue_timeouts == 0
            assert snapshot.media_in_flight == 3
        finally:
            release.set()
        assert await asyncio.gather(*media) == [b"image"] * 3

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        governor.shutdown(wait=True)


def test_background_text_is_not_blocked_by_dnd_image_generation():
    # DnD images run in the background lane; they must not take the single
    # background text slot either.
    governor = _governor(maximum=3, background=1, media=2, queue=0.05)
    release = threading.Event()

    def slow_generation():
        release.wait(timeout=2)
        return b"scene"

    async def scenario():
        with ai_execution_lane("background"):
            image = asyncio.create_task(governor.run_media_async("dnd-image", slow_generation))
            try:
                await _wait_until(lambda: governor.snapshot().media_in_flight == 1)
                assert await asyncio.to_thread(governor.run, "dnd-text", lambda: "turn") == "turn"
                assert governor.snapshot().queue_timeouts == 0
            finally:
                release.set()
            assert await image == b"scene"

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        governor.shutdown(wait=True)


def test_production_media_providers_use_media_lane(monkeypatch):
    governor = _governor(maximum=3, media=3, queue=0.05)
    monkeypatch.setattr(execution, "_default_governor", governor)
    release = threading.Event()

    def slow_sync(*_args):
        release.wait(timeout=2)
        return b"image"

    async def slow_pollinations(prompt):
        return slow_sync()

    async def unused_provider(*_args):
        return None

    monkeypatch.setattr(aihorde_image, "_generate_aihorde_image_sync", slow_sync)
    monkeypatch.setattr(gigachat_image, "_generate_gigachat_image_sync", slow_sync)
    picgeneration = SimpleNamespace(
        pollinations_generate=slow_pollinations,
        hf_generate=unused_provider,
        cf_generate_t2i=unused_provider,
        generate_gradio_img2img=unused_provider,
        handle_edit_command=unused_provider,
    )
    media_execution.install_into_picgeneration(picgeneration)

    async def scenario():
        media = [
            asyncio.create_task(aihorde_image.generate_aihorde_image("horde")),
            asyncio.create_task(gigachat_image.generate_gigachat_image("giga")),
            asyncio.create_task(picgeneration.pollinations_generate("flux")),
        ]
        try:
            await _wait_until(lambda: governor.snapshot().media_in_flight == 3)
            assert governor.snapshot().in_flight == 0

            reply = await asyncio.to_thread(
                execution.run_ai_provider_call,
                "model.generate_content",
                lambda: "reply",
            )
            assert reply == "reply"
            assert governor.snapshot().queue_timeouts == 0
        finally:
            release.set()
        assert await asyncio.gather(*media) == [b"image"] * 3

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        governor.shutdown(wait=True)


def test_media_lane_limit_is_enforced():
    governor = _governor(media=2, queue=1.0)
    release = threading.Event()
    state_lock = threading.Lock()
    running = 0
    peak = 0

    def tracked_generation():
        nonlocal running, peak
        with state_lock:
            running += 1
            peak = max(peak, running)
        try:
            release.wait(timeout=2)
            return b"video"
        finally:
            with state_lock:
                running -= 1

    async def scenario():
        tasks = [
            asyncio.create_task(governor.run_media_async(f"video-{index}", tracked_generation))
            for index in range(4)
        ]
        try:
            await _wait_until(
                lambda: governor.snapshot().media_in_flight == 2
                and governor.snapshot().media_waiting == 2
            )
            # Give a queued call a chance to overtake the limit if it could.
            await asyncio.sleep(0.05)
            assert peak == 2
        finally:
            release.set()
        assert await asyncio.gather(*tasks) == [b"video"] * 4

    try:
        asyncio.run(scenario())
        snapshot = governor.snapshot()
        assert peak == 2
        assert snapshot.media_completed == 4
        assert snapshot.media_queue_timeouts == 0
        assert snapshot.media_max_concurrency == 2
        # Media work is not mixed into text counters or latency windows.
        assert snapshot.completed == 0
        assert snapshot.request_samples == 0
    finally:
        release.set()
        governor.shutdown(wait=True)


def test_full_media_lane_times_out_in_queue_and_keeps_text_metrics_clean():
    governor = _governor(media=1, queue=0.05)
    release = threading.Event()

    def slow_generation():
        release.wait(timeout=2)
        return b"image"

    async def scenario():
        first = asyncio.create_task(governor.run_media_async("first", slow_generation))
        try:
            await _wait_until(lambda: governor.snapshot().media_in_flight == 1)
            with pytest.raises(AIQueueTimeoutError, match="media"):
                await governor.run_media_async("second", lambda: b"never")
            # The blocking variant shares the same media slots.
            with pytest.raises(AIQueueTimeoutError, match="media"):
                await asyncio.to_thread(governor.run_media, "third", lambda: b"never")
        finally:
            release.set()
        assert await first == b"image"

    try:
        asyncio.run(scenario())
        snapshot = governor.snapshot()
        assert snapshot.media_queue_timeouts == 2
        assert snapshot.queue_timeouts == 0
    finally:
        release.set()
        governor.shutdown(wait=True)


def test_media_calls_do_not_hold_default_executor_threads():
    governor = _governor(media=1, queue=1.0)
    release = threading.Event()

    def slow_generation():
        release.wait(timeout=2)
        return b"image"

    async def scenario():
        loop = asyncio.get_running_loop()
        # One default worker, as on a starved VPS: a single blocked thread
        # would stall every SQLite write behind it.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        tasks = [
            asyncio.create_task(governor.run_media_async(f"media-{index}", slow_generation))
            for index in range(3)
        ]
        try:
            await _wait_until(
                lambda: governor.snapshot().media_in_flight == 1
                and governor.snapshot().media_waiting == 2
            )
            stored = await asyncio.wait_for(asyncio.to_thread(lambda: "sqlite-write"), 0.5)
            assert stored == "sqlite-write"
        finally:
            release.set()
        assert await asyncio.gather(*tasks) == [b"image"] * 3

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        governor.shutdown(wait=True)


def test_media_usage_is_recorded_with_request_context():
    events = []
    execution.configure_ai_usage_recorder(
        lambda chat_id, user_id, model_name, request_type, **details: events.append(
            (chat_id, user_id, request_type, details)
        )
    )
    governor = _governor(media=1)

    async def scenario():
        with execution.ai_request_context(chat_id=-5005, user_id=9), execution.ai_feature_context("картинка"):
            return await governor.run_media_async("gigachat-image.generate", lambda: b"image")

    try:
        assert asyncio.run(scenario()) == b"image"
    finally:
        governor.shutdown(wait=True)
        execution.configure_ai_usage_recorder(None)

    assert len(events) == 1
    chat_id, user_id, request_type, details = events[0]
    assert (chat_id, user_id, request_type) == (-5005, 9, "gigachat-image.generate")
    assert details["feature"] == "картинка"
    assert details["lane"] == "interactive"
    assert details["success"] is True
    snapshot = governor.snapshot()
    assert snapshot.media_request_samples == 1
    assert snapshot.media_completed == 1


def test_cancelled_media_call_keeps_slot_until_worker_finishes():
    governor = _governor(media=1, queue=0.05)
    release = threading.Event()

    def slow_generation():
        release.wait(timeout=2)
        return b"late"

    async def scenario():
        task = asyncio.create_task(governor.run_media_async("cancelled", slow_generation))
        await _wait_until(lambda: governor.snapshot().media_in_flight == 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The provider is still running, so its slot must stay occupied.
        with pytest.raises(AIQueueTimeoutError):
            await governor.run_media_async("next", lambda: b"no")

        release.set()
        await _wait_until(lambda: governor.snapshot().media_in_flight == 0)
        assert await governor.run_media_async("after", lambda: b"ok") == b"ok"

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        governor.shutdown(wait=True)


def test_slot_pool_serves_threads_and_coroutines_in_fifo_order():
    pool = SlotPool(1)
    order = []
    assert pool.acquire(timeout=0)

    async def scenario():
        thread_waiter = threading.Thread(
            target=lambda: (pool.acquire(timeout=2), order.append("thread"), pool.release())
        )
        thread_waiter.start()
        await asyncio.sleep(0.05)

        async def coroutine_waiter():
            assert await pool.acquire_async(timeout=2)
            order.append("coroutine")
            pool.release()

        waiter = asyncio.create_task(coroutine_waiter())
        await asyncio.sleep(0.05)
        pool.release()
        await waiter
        await asyncio.to_thread(thread_waiter.join, 2)

    asyncio.run(scenario())
    assert order == ["thread", "coroutine"]
    # Every slot is back: the pool can be taken again without waiting.
    assert pool.acquire(timeout=0)
    pool.release()


def test_slot_pool_cancelled_async_waiter_does_not_leak_slot():
    pool = SlotPool(1)

    async def scenario():
        assert await pool.acquire_async(timeout=0)
        waiter = asyncio.create_task(pool.acquire_async(timeout=2))
        await asyncio.sleep(0.01)
        # Grant and cancel in the same iteration: the slot must come back.
        pool.release()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert await pool.acquire_async(timeout=0)
        pool.release()

    asyncio.run(scenario())
    with pytest.raises(ValueError):
        pool.release()


def test_run_application_installs_sized_default_executor(monkeypatch):
    seen = {}

    class FakeApplication:
        async def run(self):
            seen["thread"] = await asyncio.to_thread(lambda: threading.current_thread().name)

    monkeypatch.setattr(bootstrap, "DEFAULT_EXECUTOR_MAX_WORKERS", 7)
    monkeypatch.setattr(bootstrap, "create_application", lambda: FakeApplication())

    asyncio.run(bootstrap.run_application())

    assert seen["thread"].startswith("upupa-default")


def test_configure_default_executor_uses_configured_size(monkeypatch):
    monkeypatch.setattr(bootstrap, "DEFAULT_EXECUTOR_MAX_WORKERS", 11)

    async def scenario():
        executor = bootstrap.configure_default_executor()
        name = await asyncio.to_thread(lambda: threading.current_thread().name)
        return executor, name

    executor, name = asyncio.run(scenario())

    assert executor._max_workers == 11
    assert name.startswith("upupa-default")
