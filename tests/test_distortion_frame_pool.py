import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests import test_smoke_imports

SMOKE_IMPORT_FIXTURES = test_smoke_imports


def _install_recording_pool(monkeypatch):
    from services import distortion

    pools = []

    class RecordingPool(ThreadPoolExecutor):
        def __init__(self, *, max_workers):
            super().__init__(max_workers=max_workers)
            self.shutdown_kwargs = None
            pools.append(self)

        def shutdown(self, wait=True, *, cancel_futures=False):
            self.shutdown_kwargs = {"wait": wait, "cancel_futures": cancel_futures}
            super().shutdown(wait=True, cancel_futures=cancel_futures)

    monkeypatch.setattr(distortion.concurrent.futures, "ProcessPoolExecutor", RecordingPool)
    return distortion, pools


def test_each_video_gets_a_pool_that_is_closed_right_after(monkeypatch):
    distortion, pools = _install_recording_pool(monkeypatch)
    processed = []

    def task(frame_path, frame_percent):
        processed.append((frame_path, frame_percent))

    async def scenario():
        await distortion.run_frame_distortion(task, [("a.png", 10), ("b.png", 20)])
        await distortion.run_frame_distortion(task, [("c.png", 30)])

    asyncio.run(scenario())

    assert sorted(processed) == [("a.png", 10), ("b.png", 20), ("c.png", 30)]
    assert len(pools) == 2
    assert all(
        pool.shutdown_kwargs == {"wait": False, "cancel_futures": True}
        for pool in pools
    )


def test_pool_is_closed_when_a_frame_fails(monkeypatch):
    distortion, pools = _install_recording_pool(monkeypatch)

    def task(_frame_path, _frame_percent):
        raise ValueError("битый кадр")

    with pytest.raises(ValueError):
        asyncio.run(distortion.run_frame_distortion(task, [("a.png", 10)]))

    assert pools[0].shutdown_kwargs == {"wait": False, "cancel_futures": True}


def test_videos_run_one_after_another(monkeypatch):
    distortion, pools = _install_recording_pool(monkeypatch)
    monkeypatch.setattr(distortion, "_frame_distortion_semaphore", asyncio.Semaphore(1))
    open_pools_seen = []

    def task(_frame_path, _frame_percent):
        open_pools_seen.append(sum(pool.shutdown_kwargs is None for pool in pools))

    async def scenario():
        await asyncio.gather(
            distortion.run_frame_distortion(task, [("a.png", 10)]),
            distortion.run_frame_distortion(task, [("b.png", 20)]),
        )

    asyncio.run(scenario())

    assert open_pools_seen == [1, 1]
