import asyncio
import logging

from app.lifecycle import TaskSupervisor


def test_task_supervisor_cancels_running_tasks_on_stop():
    async def scenario():
        started = asyncio.Event()
        finalized = asyncio.Event()

        async def worker():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalized.set()

        supervisor = TaskSupervisor()
        task = supervisor.start(worker(), name="worker")
        await started.wait()

        assert supervisor.task_count == 1
        assert supervisor.task_names == ("worker",)

        await supervisor.stop()

        assert task.cancelled()
        assert finalized.is_set()
        assert supervisor.task_count == 0

    asyncio.run(scenario())


def test_task_supervisor_logs_failed_task(caplog):
    async def scenario():
        test_logger = logging.getLogger("tests.task-supervisor")
        supervisor = TaskSupervisor(test_logger)

        async def crash():
            raise RuntimeError("boom")

        task = supervisor.start(crash(), name="broken-worker")
        try:
            await task
        except RuntimeError:
            pass

        # done-callback выполняется следующим тиком event loop.
        await asyncio.sleep(0)
        assert supervisor.task_count == 0

    with caplog.at_level(logging.ERROR, logger="tests.task-supervisor"):
        asyncio.run(scenario())

    assert "Background task broken-worker crashed" in caplog.text


def test_resilient_task_restarts_after_crash_and_marks_recovery(caplog):
    async def scenario():
        test_logger = logging.getLogger("tests.resilient-supervisor")
        supervisor = TaskSupervisor(test_logger)
        first_crash = asyncio.Event()
        restarted = asyncio.Event()
        attempts = 0

        async def worker():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                first_crash.set()
                raise RuntimeError("boom")
            restarted.set()
            await asyncio.Event().wait()

        task = supervisor.start_resilient(
            worker,
            name="scheduler",
            restart_delay_seconds=0.02,
            max_restart_delay_seconds=0.02,
        )
        await first_crash.wait()
        await asyncio.sleep(0)
        assert supervisor.recovering_task_names == ("scheduler",)

        await asyncio.wait_for(restarted.wait(), timeout=1)
        assert attempts == 2
        assert supervisor.task_names == ("scheduler",)
        assert supervisor.recovering_task_names == ()

        await supervisor.stop()
        assert task.cancelled()
        assert supervisor.task_count == 0

    with caplog.at_level(logging.ERROR, logger="tests.resilient-supervisor"):
        asyncio.run(scenario())

    assert "Background task scheduler crashed; restarting" in caplog.text


def test_resilient_task_restarts_after_unexpected_clean_exit(caplog):
    async def scenario():
        test_logger = logging.getLogger("tests.resilient-clean-exit")
        supervisor = TaskSupervisor(test_logger)
        restarted = asyncio.Event()
        attempts = 0

        async def worker():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return
            restarted.set()
            await asyncio.Event().wait()

        supervisor.start_resilient(
            worker,
            name="scheduler",
            restart_delay_seconds=0.01,
            max_restart_delay_seconds=0.01,
        )
        await asyncio.wait_for(restarted.wait(), timeout=1)
        assert attempts == 2
        await supervisor.stop()

    with caplog.at_level(logging.WARNING, logger="tests.resilient-clean-exit"):
        asyncio.run(scenario())

    assert "Background task scheduler exited unexpectedly; restarting" in caplog.text


def test_stopping_supervisor_cancels_pending_restart():
    async def scenario():
        supervisor = TaskSupervisor()
        crashed = asyncio.Event()
        attempts = 0

        async def worker():
            nonlocal attempts
            attempts += 1
            crashed.set()
            raise RuntimeError("boom")

        supervisor.start_resilient(
            worker,
            name="scheduler",
            restart_delay_seconds=0.2,
            max_restart_delay_seconds=0.2,
        )
        await crashed.wait()
        await asyncio.sleep(0)
        assert supervisor.recovering_task_names == ("scheduler",)

        await supervisor.stop()
        await asyncio.sleep(0.22)

        assert attempts == 1
        assert supervisor.task_count == 0
        assert supervisor.recovering_task_names == ()

    asyncio.run(scenario())
