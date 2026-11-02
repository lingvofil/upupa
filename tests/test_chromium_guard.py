import asyncio

import pytest

from infrastructure import chromium_guard

MB = 1024 * 1024


def _install_memory_files(
    monkeypatch,
    tmp_path,
    *,
    limit="1000000000",
    current=0,
    active_file=0,
    inactive_file=0,
    swap=0,
    available_kb=2_000_000,
):
    service_dir = tmp_path / "cgroup" / "system.slice" / "upupa_bot.service"
    service_dir.mkdir(parents=True)
    (service_dir / "memory.max").write_text(f"{limit}\n", encoding="utf-8")
    (service_dir / "memory.current").write_text(f"{current}\n", encoding="utf-8")
    (service_dir / "memory.swap.current").write_text(f"{swap}\n", encoding="utf-8")
    (service_dir / "memory.stat").write_text(
        f"anon 1\nactive_file {active_file}\ninactive_file {inactive_file}\n",
        encoding="utf-8",
    )

    proc_cgroup = tmp_path / "proc_cgroup"
    proc_cgroup.write_text("0::/system.slice/upupa_bot.service\n", encoding="utf-8")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        f"MemTotal: 1800000 kB\nMemAvailable: {available_kb} kB\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(chromium_guard, "_CGROUP_ROOT", tmp_path / "cgroup")
    monkeypatch.setattr(chromium_guard, "_PROC_CGROUP_PATH", proc_cgroup)
    monkeypatch.setattr(chromium_guard, "_PROC_MEMINFO_PATH", meminfo)


def test_free_memory_ignores_page_cache_but_counts_swap(monkeypatch, tmp_path):
    _install_memory_files(
        monkeypatch,
        tmp_path,
        limit=str(1000 * MB),
        current=800 * MB,
        active_file=100 * MB,
        inactive_file=200 * MB,
        swap=50 * MB,
    )

    assert chromium_guard.free_memory_bytes() == 450 * MB


def test_free_memory_is_limited_by_the_whole_system(monkeypatch, tmp_path):
    _install_memory_files(
        monkeypatch,
        tmp_path,
        limit=str(1000 * MB),
        available_kb=300 * 1024,
    )

    assert chromium_guard.free_memory_bytes() == 300 * MB


def test_service_without_memory_limit_falls_back_to_system(monkeypatch, tmp_path):
    _install_memory_files(monkeypatch, tmp_path, limit="max", available_kb=500 * 1024)

    assert chromium_guard.free_memory_bytes() == 500 * MB


def test_unknown_memory_does_not_block_the_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(chromium_guard, "_PROC_CGROUP_PATH", tmp_path / "missing")
    monkeypatch.setattr(chromium_guard, "_PROC_MEMINFO_PATH", tmp_path / "missing")

    async def scenario():
        async with chromium_guard.chromium_slot():
            return "launched"

    assert chromium_guard.free_memory_bytes() is None
    assert asyncio.run(scenario()) == "launched"


def test_low_memory_refuses_to_start_chromium_and_frees_the_slot(monkeypatch):
    free = {"bytes": chromium_guard.CHROMIUM_MIN_FREE_BYTES - 1}
    monkeypatch.setattr(chromium_guard, "free_memory_bytes", lambda: free["bytes"])

    async def launch():
        async with chromium_guard.chromium_slot():
            return "launched"

    with pytest.raises(chromium_guard.NotEnoughMemoryForChromium):
        asyncio.run(launch())

    free["bytes"] = chromium_guard.CHROMIUM_MIN_FREE_BYTES
    assert asyncio.run(launch()) == "launched"


def test_only_one_chromium_runs_at_a_time(monkeypatch):
    monkeypatch.setattr(chromium_guard, "_chromium_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(chromium_guard, "free_memory_bytes", lambda: None)
    running = {"now": 0, "peak": 0}

    async def launch():
        async with chromium_guard.chromium_slot():
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
            await asyncio.sleep(0)
            running["now"] -= 1

    async def scenario():
        await asyncio.gather(launch(), launch(), launch())

    asyncio.run(scenario())

    assert running["peak"] == 1
