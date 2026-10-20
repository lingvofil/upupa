"""Регрессии единого запуска подпроцессов: таймаут, отмена, лимиты, sherlock, lottie.

Вместо ffmpeg и sherlock везде запускается безвредный локальный python-процесс.
"""

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import test_smoke_imports

_ = test_smoke_imports  # env + heavy dependency mocks

from infrastructure import subprocess_runner
from infrastructure.subprocess_runner import SubprocessResult, run_subprocess


ROOT = Path(__file__).resolve().parents[1]
HANGING_COMMAND = [sys.executable, "-c", "import time; time.sleep(60)"]


def _capture_processes(monkeypatch):
    processes = []
    created = asyncio.Event()
    original = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        proc = await original(*args, **kwargs)
        processes.append(proc)
        created.set()
        return proc

    monkeypatch.setattr(subprocess_runner.asyncio, "create_subprocess_exec", spawn)
    return processes, created


async def _cleanup(processes):
    for proc in processes:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


def test_hanging_process_is_killed_on_timeout(monkeypatch):
    async def scenario():
        processes, _created = _capture_processes(monkeypatch)
        started = time.monotonic()
        try:
            result = await run_subprocess(HANGING_COMMAND, timeout=0.5)
            elapsed = time.monotonic() - started
            assert result.timed_out is True
            assert result.ok is False
            assert elapsed < 10, f"Timeout did not stop the process quickly: {elapsed:.1f}s"
            assert len(processes) == 1
            assert processes[0].returncode is not None, "Timed out process is still running"
        finally:
            await _cleanup(processes)

    asyncio.run(scenario())


def test_cancelled_task_kills_process_and_reraises(monkeypatch):
    async def scenario():
        processes, created = _capture_processes(monkeypatch)
        task = asyncio.create_task(run_subprocess(HANGING_COMMAND, timeout=60))
        try:
            await asyncio.wait_for(created.wait(), timeout=10)
            await asyncio.sleep(0.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert processes[0].returncode is not None, "Cancelled request left its child process running"
        finally:
            await _cleanup(processes)

    asyncio.run(scenario())


def test_captured_output_is_bounded_without_deadlock():
    script = (
        "import sys\n"
        "sys.stdout.write('START' + 'x' * 3_000_000)\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('y' * 3_000_000 + 'END')\n"
        "sys.stderr.flush()\n"
    )
    result = asyncio.run(
        run_subprocess([sys.executable, "-c", script], timeout=30, max_output_bytes=4096)
    )

    assert result.ok is True
    assert len(result.stdout) == 4096
    assert result.stdout.startswith(b"START"), "stdout must keep its head for parsers"
    assert result.stdout_truncated is True
    assert len(result.stderr) == 4096
    assert result.stderr.endswith(b"END"), "stderr must keep its tail with the final error"
    assert result.stderr_truncated is True


def test_timeout_is_mandatory():
    with pytest.raises(TypeError):
        asyncio.run(run_subprocess([sys.executable, "-c", "pass"]))
    for bad_timeout in (None, 0, -1):
        with pytest.raises(ValueError):
            asyncio.run(run_subprocess([sys.executable, "-c", "pass"], timeout=bad_timeout))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return True
    return state != "Z"


@pytest.mark.skipif(os.name != "posix", reason="process groups exist only on POSIX")
def test_timeout_kills_whole_process_group():
    # Ребёнок запускает внука (как ffmpeg-обёртки) и печатает его pid.
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    result = asyncio.run(run_subprocess([sys.executable, "-c", script], timeout=1.5))
    assert result.timed_out is True
    grandchild_pid = int(result.stdout_text.split()[0])

    deadline = time.monotonic() + 5
    while _pid_alive(grandchild_pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    alive = _pid_alive(grandchild_pid)
    if alive:
        os.kill(grandchild_pid, 9)
    assert not alive, "Grandchild survived the process group kill"


def test_distortion_semaphore_is_released_after_ffmpeg_timeout(monkeypatch):
    from services import distortion

    real_run_subprocess = distortion.run_subprocess
    seen_timeouts = []

    async def hanging_ffmpeg(command, **kwargs):
        seen_timeouts.append(kwargs["timeout"])
        return await real_run_subprocess(HANGING_COMMAND, **kwargs)

    monkeypatch.setattr(distortion, "run_subprocess", hanging_ffmpeg)
    monkeypatch.setattr(distortion, "FFMPEG_VIDEO_DISTORTION_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(distortion, "processing_semaphore", asyncio.Semaphore(1))

    async def scenario():
        processes, _created = _capture_processes(monkeypatch)
        try:
            ok = await asyncio.wait_for(
                distortion.apply_ffmpeg_video_distortion("in.mp4", "out.mp4", 50),
                timeout=15,
            )
            assert ok is False
            assert seen_timeouts == [0.5]
            assert not distortion.processing_semaphore.locked(), "Hung ffmpeg kept the distortion semaphore"
            assert processes[0].returncode is not None

            # Следующий запрос не ждёт рестарта бота.
            ok = await asyncio.wait_for(
                distortion.apply_ffmpeg_video_distortion("in.mp4", "out.mp4", 50),
                timeout=15,
            )
            assert ok is False
            assert len(seen_timeouts) == 2
        finally:
            await _cleanup(processes)

    asyncio.run(scenario())


def test_ffprobe_helpers_have_timeouts(monkeypatch):
    from services import distortion, video_note_branding

    calls = []

    async def fake_run(command, **kwargs):
        calls.append((command[0], kwargs["timeout"]))
        return SubprocessResult(returncode=None, stdout=b"", stderr=b"", timed_out=True, duration_seconds=30.0)

    monkeypatch.setattr(distortion, "run_subprocess", fake_run)
    monkeypatch.setattr(video_note_branding, "run_subprocess", fake_run)

    assert asyncio.run(distortion.get_media_info("in.mp4")) is None
    with pytest.raises(RuntimeError):
        asyncio.run(video_note_branding._probe_video_size("in.mp4"))
    ok, _ = asyncio.run(video_note_branding._run_ffmpeg(["ffmpeg", "-i", "in.mp4"]))
    assert ok is False
    assert calls == [
        ("ffprobe", distortion.FFPROBE_TIMEOUT_SECONDS),
        ("ffprobe", video_note_branding.FFPROBE_TIMEOUT_SECONDS),
        ("ffmpeg", video_note_branding.FFMPEG_TIMEOUT_SECONDS),
    ]
    assert distortion.FFPROBE_TIMEOUT_SECONDS <= 30
    assert video_note_branding.FFMPEG_TIMEOUT_SECONDS <= 180


def _sherlock_message(user_id, text="упупа ищи someone"):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=-100500),
        reply=AsyncMock(),
    )


def _replies(message):
    return [call.args[0] for call in message.reply.await_args_list]


@pytest.fixture
def fresh_sherlock_state(monkeypatch):
    from services import sherlock

    monkeypatch.setattr(sherlock, "_sherlock_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(sherlock, "_last_search_started", {})
    return sherlock


def test_sherlock_rejects_parallel_search(monkeypatch, fresh_sherlock_state):
    sherlock = fresh_sherlock_state
    started, release = asyncio.Event(), asyncio.Event()
    searched = []

    async def slow_search(self, username):
        searched.append(username)
        started.set()
        await release.wait()
        return {"username": username, "found": [], "raw_output": "", "error": None}

    monkeypatch.setattr(sherlock.SherlockSearch, "search", slow_search)

    async def scenario():
        first = _sherlock_message(1)
        second = _sherlock_message(2)
        task = asyncio.create_task(sherlock.process_sherlock_command(first))
        await asyncio.wait_for(started.wait(), timeout=5)

        await sherlock.process_sherlock_command(second)
        assert searched == ["someone"], "Second search ran in parallel"
        assert "уже ищу" in _replies(second)[-1]

        release.set()
        await asyncio.wait_for(task, timeout=5)
        assert not sherlock._sherlock_semaphore.locked()

    asyncio.run(scenario())


def test_sherlock_applies_per_user_cooldown(monkeypatch, fresh_sherlock_state):
    sherlock = fresh_sherlock_state
    searched = []

    async def quick_search(self, username):
        searched.append(username)
        return {"username": username, "found": ["https://example.test/someone"], "raw_output": "", "error": None}

    monkeypatch.setattr(sherlock.SherlockSearch, "search", quick_search)

    async def scenario():
        await sherlock.process_sherlock_command(_sherlock_message(1))
        repeated = _sherlock_message(1)
        await sherlock.process_sherlock_command(repeated)
        other_user = _sherlock_message(2)
        await sherlock.process_sherlock_command(other_user)

        assert searched == ["someone", "someone"]
        assert "недавно" in _replies(repeated)[-1]
        assert _replies(other_user)[-1] == "https://example.test/someone"

    asyncio.run(scenario())


def test_sherlock_passes_username_after_end_of_options(monkeypatch, fresh_sherlock_state):
    sherlock = fresh_sherlock_state
    calls = []

    async def fake_run(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        return SubprocessResult(returncode=0, stdout=b"", stderr=b"", timed_out=False, duration_seconds=0.1)

    monkeypatch.setattr(sherlock, "run_subprocess", fake_run)

    result = asyncio.run(sherlock.SherlockSearch().search("--output=/etc/passwd"))

    assert result["error"] is None
    command, timeout = calls[0]
    assert command == [sys.executable, "-m", "sherlock_project", "--", "--output=/etc/passwd"]
    assert timeout == sherlock.SherlockSearch.TIMEOUT_SECONDS


def test_find_venv_executable_prefers_interpreter_directory(monkeypatch, tmp_path):
    bin_dir = tmp_path / "srv" / "upupa" / "venv" / "bin"
    bin_dir.mkdir(parents=True)
    script = bin_dir / "lottie_convert.py"
    script.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    monkeypatch.setattr(subprocess_runner.sys, "executable", str(bin_dir / "python"))
    monkeypatch.setattr(subprocess_runner.shutil, "which", lambda name: "/elsewhere/" + name)

    assert subprocess_runner.find_venv_executable("lottie_convert.py") == str(script)


def test_find_venv_executable_falls_back_to_path(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess_runner.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(subprocess_runner.shutil, "which", lambda name: "/usr/local/bin/" + name)
    assert subprocess_runner.find_venv_executable("lottie_convert.py") == "/usr/local/bin/lottie_convert.py"

    monkeypatch.setattr(subprocess_runner.shutil, "which", lambda name: None)
    assert subprocess_runner.find_venv_executable("lottie_convert.py") is None


@pytest.mark.parametrize("module_name", ["distortion", "media_change", "ytp"])
def test_tgs_conversion_uses_discovered_lottie_convert(monkeypatch, module_name):
    import importlib

    module = importlib.import_module(f"services.{module_name}")
    commands = []
    discovered = "/srv/upupa/venv/bin/lottie_convert.py"

    async def fake_run_subprocess(command, **kwargs):
        commands.append(command)
        return SubprocessResult(returncode=0, stdout=b"", stderr=b"", timed_out=False, duration_seconds=0.1)

    monkeypatch.setattr(module, "run_subprocess", fake_run_subprocess)
    convert = getattr(module, "convert_tgs_to_webm", None) or module._convert_tgs_to_webm

    monkeypatch.setattr(module, "find_venv_executable", lambda name: discovered)
    assert asyncio.run(convert("in.tgs", "out.webm")) is True
    assert commands[-1][0] == discovered

    monkeypatch.setattr(module, "find_venv_executable", lambda name: None)
    commands.clear()
    assert asyncio.run(convert("in.tgs", "out.webm")) is False
    assert commands == []


def test_no_hardcoded_deploy_paths_for_lottie():
    for name in ("distortion.py", "media_change.py", "ytp.py"):
        source = (ROOT / "services" / name).read_text(encoding="utf-8")
        assert "/root/upupa/venv" not in source, f"services/{name} hardcodes the /root/upupa venv"


def test_production_code_spawns_processes_only_through_runner():
    production_dirs = ["AI", "app", "core", "features", "games", "handlers", "infrastructure", "prompts", "services"]
    allowed = ROOT / "infrastructure" / "subprocess_runner.py"
    offenders = []
    for directory in production_dirs:
        for path in (ROOT / directory).rglob("*.py"):
            if path == allowed:
                continue
            source = path.read_text(encoding="utf-8")
            if "create_subprocess_exec" in source or "create_subprocess_shell" in source:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], (
        "Внешние процессы запускаются только через infrastructure.subprocess_runner "
        f"(таймаут и убийство при отмене): {offenders}"
    )
